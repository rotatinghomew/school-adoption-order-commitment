"""HTTP 接口冒烟测试，并与真实合作方伙伴服务做端到端演练。"""

import json
import unittest
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from service.api import build_server

from partner.server import Partner

from tests.support import DATA_DIR


PARTNER_CONTRACT = json.loads(
    (Path(__file__).resolve().parents[1] / "partner" / "exchange.json").read_text(
        encoding="utf-8"
    )
)


class HttpSmokeTests(unittest.TestCase):
    def setUp(self):
        # 真实伙伴服务（不模拟断连），选用后端通过 HTTP 调用它
        self.partner = Partner(0, PARTNER_CONTRACT, disconnect=False)
        self.partner_thread = Thread(
            target=self.partner.serve_forever, kwargs={"poll_interval": 0.02}
        )
        self.partner_thread.start()
        partner_url = f"http://127.0.0.1:{self.partner.server_address[1]}"

        self.server = build_server(0, DATA_DIR, partner_base_url=partner_url)
        self.server_thread = Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}
        )
        self.server_thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server_thread.join, 2)
        self.addCleanup(self.partner.shutdown)
        self.addCleanup(self.partner.server_close)
        self.addCleanup(self.partner_thread.join, 2)

    def _call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self.base + path, data=data, method=method,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_full_journey_confirmation_resolution_and_procurement(self):
        # 两所中心校确认 set-01
        for center in ("c1", "c2"):
            status, _ = self._call(
                "POST", "/schemes/set-01/confirmations",
                {"center": center, "day": "2026-09-05"},
            )
            self.assertEqual(status, 200)
        # 学校查 k1 当前可按 vol-a 开课
        status, view = self._call("GET", "/classrooms/k1?day=2026-09-06")
        self.assertEqual(status, 200)
        self.assertEqual(view["committable_book"], "vol-a")
        # 采购报订，合作方真实受理并完成
        status, order = self._call("POST", "/procurement/reservations", {
            "operation_id": "http-op-1", "supplier": "press-1",
            "classroom": "k1", "edition": "vol-a",
            "quantity": 25, "day": "2026-09-06",
        })
        self.assertEqual(status, 202)
        self.assertEqual(order["effective_quantity"], 25)
        # 采购按班查有效数量
        status, status_view = self._call(
            "GET", "/procurement/classrooms/k1?day=2026-09-20"
        )
        self.assertEqual(status_view["effective_quantity"], 25)
        # 走交换契约减单到 10
        status, adjusted = self._call("POST", "/procurement/adjustments", {
            "operation_id": "http-adj-1", "supplier": "press-1",
            "classroom": "k1", "edition": "vol-a", "quantity": 10,
            "reserve_operation_id": "http-op-1", "day": "2026-09-20",
        })
        self.assertEqual(status, 202)
        self.assertEqual(adjusted["effective_quantity"], 10)

    def test_domain_error_maps_to_http_status(self):
        # 无授权申报 -> 403
        status, payload = self._call("POST", "/applications", {
            "id": "bad-app", "center": "c3", "classroom": "k1",
            "subject": "地理", "book": "vol-a",
            "term": "2026秋", "day": "2026-09-05",
        })
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "forbidden")
        # 未知路由 -> 404
        status, payload = self._call("GET", "/nope")
        self.assertEqual(status, 404)

    def test_conflict_blocks_reservation_until_arbitration(self):
        self._call(
            "POST", "/schemes/set-01/confirmations",
            {"center": "c1", "day": "2026-09-05"},
        )
        self._call(
            "POST", "/schemes/set-01/confirmations",
            {"center": "c2", "day": "2026-09-05"},
        )
        # set-02 的 c3 片区 09-20 才生效
        for center in ("c2", "c3"):
            self._call(
                "POST", "/schemes/set-02/confirmations",
                {"center": center, "day": "2026-09-20"},
            )
        status, order = self._call("POST", "/procurement/reservations", {
            "operation_id": "blocked-1", "supplier": "press-1",
            "classroom": "k2", "edition": "vol-a",
            "quantity": 5, "day": "2026-09-21",
        })
        self.assertEqual(status, 409)
        self.assertEqual(order["error"], "uncommittable")
        status, _ = self._call("POST", "/classrooms/k2/arbitration", {
            "book": "vol-a", "day": "2026-09-22", "reason": "裁决沿用 vol-a",
        })
        self.assertEqual(status, 200)
        status, order = self._call("POST", "/procurement/reservations", {
            "operation_id": "blocked-2", "supplier": "press-1",
            "classroom": "k2", "edition": "vol-a",
            "quantity": 5, "day": "2026-09-22",
        })
        self.assertEqual(status, 202)
        self.assertEqual(order["effective_quantity"], 5)


if __name__ == "__main__":
    unittest.main()
