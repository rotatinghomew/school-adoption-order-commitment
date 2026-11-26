"""教务与采购共用的 HTTP 接口（标准库实现）。

所有端点收发 UTF-8 JSON，日期参数走请求体或查询串 ``day``；
领域错误按 :class:`~service.errors.ServiceError.status` 映射状态码。

教务端点::

    POST /applications                 中心校申报（去重，禁止同班重复计入）
    POST /exceptions                   运行期纳入校点个别授权
    GET  /classrooms/{id}/authority    谁能为该班该课作选择
    GET  /classrooms/{id}/membership   当前归属依据与调动待核范围
    GET  /schemes/{id}/scope           去重去例外后的适用范围
    POST /schemes/{id}/confirmations   中心校确认（绑定范围签名）
    POST /schemes/{id}/objections      有理由异议，旧确认失效
    POST /classrooms/{id}/arbitration  县教务裁决冲突选择
    POST /classrooms/{id}/pending-moves 期中调动待核
    POST /classrooms/{id}/switches     确认切换课次
    POST /books/{id}/suspensions       书目暂停
    POST /classrooms/{id}/fallback     转后备选用
    GET  /classrooms/{id}              学校查当前按什么书开课

采购端点::

    POST /procurement/reservations     正式报订承诺
    POST /procurement/adjustments      按交换契约减单/改货
    GET  /procurement/operations/{id}  轮询合作方回执
    POST /procurement/fallback         后备只覆盖未承诺册数
    GET  /procurement/classrooms/{id}  按班查有效数量与待调整原因
"""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    from .errors import NotFound, ServiceError
    from .exchange import ExchangeClient
    from .repository import Catalog
    from .service import SelectionService
except ImportError:  # 允许 `python3 service/api.py` 直接启动
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from service.errors import NotFound, ServiceError
    from service.exchange import ExchangeClient
    from service.repository import Catalog
    from service.service import SelectionService


def _json_default(value):
    if isinstance(value, (set, tuple)):
        return list(value)
    raise TypeError(f"无法序列化 {type(value)!r}")


class SelectionHTTPHandler(BaseHTTPRequestHandler):
    server_version = "SelectionBackend/1.0"

    # -- 基础收发 -------------------------------------------------------

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 1 <= length <= 1_048_576:
            raise ValueError("请求体长度不合法")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return body

    def _query_day(self, parsed):
        values = parse_qs(parsed.query).get("day")
        return values[0] if values else None

    def _handle(self, handler):
        try:
            payload = self._read_body() if self.command == "POST" else {}
            status, result = handler(payload)
            self._send(status, result)
        except ServiceError as exc:
            self._send(exc.status, exc.to_dict())
        except (ValueError, KeyError, TypeError) as exc:
            self._send(400, {"error": "invalid_input", "message": str(exc), "details": {}})

    def log_message(self, fmt, *args):  # 与伙伴服务保持安静的测试输出
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # -- 路由 -----------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        service = self.server.service
        day = self._query_day(parsed)

        def static_routes():
            match = re.fullmatch(r"/classrooms/([A-Za-z0-9_-]+)", path)
            if match:
                return 200, service.resolve_classroom(match.group(1), day)
            match = re.fullmatch(r"/classrooms/([A-Za-z0-9_-]+)/authority", path)
            if match:
                return 200, service.parties_for(match.group(1), day)
            match = re.fullmatch(r"/classrooms/([A-Za-z0-9_-]+)/membership", path)
            if match:
                return 200, service.membership(match.group(1), day)
            match = re.fullmatch(r"/schemes/([A-Za-z0-9_-]+)/scope", path)
            if match:
                return 200, service.scheme_scope(match.group(1), day)
            match = re.fullmatch(
                r"/procurement/classrooms/([A-Za-z0-9_-]+)", path
            )
            if match:
                return 200, service.order_status(match.group(1), day)
            match = re.fullmatch(
                r"/procurement/operations/([A-Za-z0-9_-]+)", path
            )
            if match:
                result = service.procurement.poll_adjustment(match.group(1))
                if result is None:
                    raise NotFound("未知操作号", details={"operation_id": match.group(1)})
                return 200, result
            raise NotFound(f"接口不存在: {path}")

        self._handle(lambda _body: static_routes())

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        service = self.server.service

        def routes(body):
            if path == "/applications":
                return 201, service.submit_application(body)
            if path == "/exceptions":
                return 201, service.intake_exception(body)

            match = re.fullmatch(r"/schemes/([A-Za-z0-9_-]+)/confirmations", path)
            if match:
                return 200, service.confirm_scheme(
                    match.group(1), body["center"], body["day"]
                )
            match = re.fullmatch(r"/schemes/([A-Za-z0-9_-]+)/objections", path)
            if match:
                return 200, service.object_scheme(
                    match.group(1), body["center"], body["day"], body["reason"]
                )
            match = re.fullmatch(r"/classrooms/([A-Za-z0-9_-]+)/arbitration", path)
            if match:
                return 200, service.arbitrate(
                    match.group(1), body["book"], body["day"], body["reason"]
                )
            match = re.fullmatch(r"/classrooms/([A-Za-z0-9_-]+)/pending-moves", path)
            if match:
                return 201, service.register_pending_move(
                    match.group(1), body["from_center"], body["to_center"],
                    body["not_before"], body["reason"],
                )
            match = re.fullmatch(r"/classrooms/([A-Za-z0-9_-]+)/switches", path)
            if match:
                return 200, service.confirm_switch(
                    match.group(1), body["book"], body["day"], body["lesson"]
                )
            match = re.fullmatch(r"/classrooms/([A-Za-z0-9_-]+)/fallback", path)
            if match:
                return 200, service.transfer_to_fallback(
                    match.group(1), body["day"], body.get("reason")
                )
            match = re.fullmatch(r"/books/([A-Za-z0-9_-]+)/suspensions", path)
            if match:
                service.suspend_book(match.group(1), body["day"], body["reason"])
                return 201, {"book": match.group(1), "suspended_on": body["day"]}

            if path == "/procurement/reservations":
                return 202, service.procurement.reserve(
                    operation_id=body["operation_id"],
                    supplier=body["supplier"],
                    classroom_id=body["classroom"],
                    edition=body["edition"],
                    quantity=body["quantity"],
                    day=body["day"],
                )
            if path == "/procurement/adjustments":
                return 202, service.procurement.adjust(
                    operation_id=body["operation_id"],
                    supplier=body["supplier"],
                    classroom_id=body["classroom"],
                    edition=body["edition"],
                    quantity=body["quantity"],
                    reserve_operation_id=body["reserve_operation_id"],
                    day=body["day"],
                )
            if path == "/procurement/fallback":
                return 202, service.procurement.reserve_fallback(
                    operation_id=body["operation_id"],
                    supplier=body["supplier"],
                    classroom_id=body["classroom"],
                    quantity=body["quantity"],
                    day=body["day"],
                )
            raise NotFound(f"接口不存在: {path}")

        self._handle(routes)


def build_server(port, data_dir, partner_base_url=None, verbose=False):
    """组装 HTTP 服务：目录 + 状态 + 合作方客户端。"""
    catalog = Catalog.load(data_dir)
    exchange = ExchangeClient(partner_base_url) if partner_base_url else None
    service = SelectionService(catalog, exchange)
    server = ThreadingHTTPServer(("127.0.0.1", port), SelectionHTTPHandler)
    server.service = service
    server.verbose = verbose
    return server


def main():
    import argparse

    parser = argparse.ArgumentParser(description="启动选用后端 HTTP 服务")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--partner-url", default=None,
                        help="合作方基址，如 http://127.0.0.1:8087；不填则采购端点仅做本地校验")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    server = build_server(args.port, args.data_dir, args.partner_url, args.verbose)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
