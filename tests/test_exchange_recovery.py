"""与真实合作方伙伴服务演练断连恢复与同号幂等。"""

import json
import unittest
from pathlib import Path
from threading import Thread

from service.api import build_server

from partner.server import Partner

from tests.support import DATA_DIR


CONTRACT = json.loads(
    (Path(__file__).resolve().parents[1] / "partner" / "exchange.json").read_text(
        encoding="utf-8"
    )
)


class DisconnectRecoveryTests(unittest.TestCase):
    def setUp(self):
        # 伙伴在“首次受理新单后断连”模式下运行
        self.partner = Partner(0, CONTRACT, disconnect=True)
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
        self.service = self.server.service
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server_thread.join, 2)
        self.addCleanup(self.partner.shutdown)
        self.addCleanup(self.partner.server_close)
        self.addCleanup(self.partner_thread.join, 2)

    def test_accepted_disconnect_is_recovered_with_same_operation_id(self):
        self.service.confirm_scheme("set-01", "c1", "2026-09-05")
        self.service.confirm_scheme("set-01", "c2", "2026-09-05")
        # 真实伙伴受理后断连；客户端必须凭原操作号轮询拿到完成回执
        view = self.service.procurement.reserve(
            operation_id="recover-1", supplier="press-1",
            classroom_id="k1", edition="vol-a", quantity=25, day="2026-09-06",
        )
        self.assertEqual(view["commitment"]["state"], "completed")
        self.assertEqual(view["commitment"]["receipt_id"], "demo-recover-1")
        # 伙伴侧确实只保留了同一操作号的一张受理单
        self.assertIn("recover-1", self.partner.receipts)
        self.assertEqual(len(self.partner.receipts), 1)


if __name__ == "__main__":
    unittest.main()
