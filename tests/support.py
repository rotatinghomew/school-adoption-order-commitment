"""测试共用工厂。"""

import unittest
from pathlib import Path

from service import Catalog, FakeExchange, SelectionService

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        # 每个用例独立状态；FakeExchange 默认立即给出完成回执
        self.service = SelectionService(Catalog.load(DATA_DIR), FakeExchange())

    def scope(self, scheme_id, day):
        return self.service.scheme_scope(scheme_id, day)

    def confirm_both(self, scheme_id, day):
        scheme = self.service.catalog.scheme(scheme_id)
        for center in scheme["centers"]:
            self.service.confirm_scheme(scheme_id, center, day)
