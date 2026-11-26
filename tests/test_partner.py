"""验证本地合作方的受理语义与 HTTP 回执。"""

import http.client
import json
from pathlib import Path
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from partner.server import Partner


SOURCE = Path(__file__).resolve().parents[1] / "partner"


class PartnerTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((SOURCE / "exchange.json").read_text(encoding="utf-8"))
        self.body = json.loads((SOURCE / "sample.json").read_text(encoding="utf-8"))
        self.server = Partner(0, self.contract, disconnect=True)
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02})
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1]) + self.contract["route"]
        self.operation = self.url + "/" + self.body["operation_id"]

    def submit(self, body):
        request = Request(self.url, data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
        return urlopen(request, timeout=2)

    def test_partner_accepts_before_connection_closes_and_keeps_original(self):
        with self.assertRaises(http.client.RemoteDisconnected):
            self.submit(self.body)
        with urlopen(self.operation, timeout=2) as response:
            self.assertEqual(json.load(response)["state"], "accepted")
        with self.submit(self.body) as response:
            self.assertEqual(response.status, 202)
        with urlopen(self.operation, timeout=2) as response:
            receipt = json.load(response)
        self.assertEqual(receipt["state"], "completed")
        self.assertEqual(receipt["request"], self.body)
        with self.assertRaises(HTTPError) as failed:
            self.submit(dict(self.body, note="同号的另一张受理单"))
        self.assertEqual(failed.exception.code, 409)

    def test_unknown_receipt_and_missing_required_inputs(self):
        with self.assertRaises(HTTPError) as missing:
            urlopen(self.operation, timeout=2)
        self.assertEqual(missing.exception.code, 404)
        required = self.contract["actions"][self.body["action"]][0]
        incomplete = {key: value for key, value in self.body.items() if key != required}
        with self.assertRaises(HTTPError) as invalid:
            self.submit(incomplete)
        self.assertEqual(invalid.exception.code, 400)
