"""在本机模拟现有合作方的 HTTP 受理与回执。"""

import argparse
import json
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock


class Partner(ThreadingHTTPServer):
    def __init__(self, port, contract, disconnect):
        super().__init__(("127.0.0.1", port), Exchange)
        self.contract = contract
        self.disconnect = disconnect
        self.receipts = {}
        self.lock = Lock()


class Exchange(BaseHTTPRequestHandler):
    def reply(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        contract = self.server.contract
        if self.path != contract["route"]:
            self.reply(404, {"error": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 65536:
                raise ValueError
            body = json.loads(self.rfile.read(length).decode())
            if not isinstance(body, dict):
                raise ValueError
            key = body.get("operation_id")
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
                raise ValueError
            required = contract["actions"].get(body.get("action"))
            if required is None or any(field not in body or body[field] is None for field in required):
                raise ValueError
        except (TypeError, UnicodeError, ValueError):
            self.reply(400, {"error": "受理单无法识别"})
            return
        with self.server.lock:
            previous = self.server.receipts.get(key)
            if previous is not None and previous["request"] != body:
                self.reply(409, {"error": "相同操作号关联了不同受理单"})
                return
            if previous is None:
                self.server.receipts[key] = {"request": body, "reads": 0}
            lose_response = previous is None and self.server.disconnect
        if lose_response:
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.reply(202, {"operation_id": key, "state": "accepted"})

    def do_GET(self):
        prefix = self.server.contract["route"] + "/"
        if not self.path.startswith(prefix):
            self.reply(404, {"error": "接口不存在"})
            return
        key = self.path.removeprefix(prefix)
        with self.server.lock:
            receipt = self.server.receipts.get(key)
            if receipt is None:
                result = None
            else:
                receipt["reads"] += 1
                result = {"operation_id": key, "state": "accepted"}
                if receipt["reads"] >= 2:
                    result.update(state="completed", receipt_id="demo-" + key, request=receipt["request"])
        if result is None:
            self.reply(404, {"error": "未找到该操作号"})
        else:
            self.reply(200, result)


def main():
    parser = argparse.ArgumentParser(description="启动本地合作方演练接口")
    parser.add_argument("--port", type=int, default=8087)
    parser.add_argument("--disconnect-after-accept", action="store_true")
    args = parser.parse_args()
    contract = json.loads(Path(__file__).with_name("exchange.json").read_text(encoding="utf-8"))
    with Partner(args.port, contract, args.disconnect_after_accept) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
