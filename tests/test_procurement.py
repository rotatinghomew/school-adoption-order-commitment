"""采购承诺、交换契约与后备出单规则。"""

from service import FakeExchange, Uncommittable, ValidationError

from tests.support import DATA_DIR, ServiceTestCase
from service import Catalog, SelectionService


class ProcurementTests(ServiceTestCase):
    def _bind_set01(self):
        self.confirm_both("set-01", "2026-09-05")

    def test_reserve_requires_unique_committable_choice(self):
        # 没有任何有效选择时不能报订
        with self.assertRaises(Uncommittable):
            self.service.procurement.reserve(
                operation_id="op-1", supplier="press-1",
                classroom_id="k1", edition="vol-a", quantity=25, day="2026-09-05",
            )
        self._bind_set01()
        view = self.service.procurement.reserve(
            operation_id="op-1", supplier="press-1",
            classroom_id="k1", edition="vol-a", quantity=25, day="2026-09-06",
        )
        self.assertEqual(view["effective_quantity"], 25)
        self.assertEqual(view["commitment"]["state"], "completed")

    def test_one_classroom_one_subject_one_final_edition(self):
        self._bind_set01()
        self.service.procurement.reserve(
            operation_id="op-1", supplier="press-1",
            classroom_id="k1", edition="vol-a", quantity=10, day="2026-09-06",
        )
        # 报订书种与终选不一致 -> 未确认一方不能抢占
        with self.assertRaises(Uncommittable):
            self.service.procurement.reserve(
                operation_id="op-2", supplier="press-2",
                classroom_id="k1", edition="vol-b", quantity=5, day="2026-09-06",
            )

    def test_reserve_quantity_cannot_exceed_uncovered_capacity(self):
        self._bind_set01()
        self.service.procurement.reserve(
            operation_id="op-1", supplier="press-1",
            classroom_id="k2", edition="vol-a", quantity=10, day="2026-09-06",
        )
        with self.assertRaises(ValidationError) as ctx:
            self.service.procurement.reserve(
                operation_id="op-2", supplier="press-1",
                classroom_id="k2", edition="vol-a", quantity=20, day="2026-09-06",
            )
        self.assertEqual(ctx.exception.details["free_capacity"], 8)

    def test_idempotent_operation_id_with_same_input(self):
        self._bind_set01()
        kwargs = dict(
            supplier="press-1", classroom_id="k1",
            edition="vol-a", quantity=10, day="2026-09-06",
        )
        first = self.service.procurement.reserve(operation_id="op-9", **kwargs)
        second = self.service.procurement.reserve(operation_id="op-9", **kwargs)
        self.assertFalse(first["repeated_operation"])
        self.assertTrue(second["repeated_operation"])
        self.assertEqual(second["effective_quantity"], 10, "重复受理不重复占册")

    def test_adjust_keeps_original_quantity_while_exchange_outcome_unknown(self):
        self._bind_set01()
        self.service.procurement.reserve(
            operation_id="op-r", supplier="press-1",
            classroom_id="k2", edition="vol-a", quantity=18, day="2026-09-06",
        )
        # 交换结果未知的假伙伴：poll 永远返回 None
        service = SelectionService(Catalog.load(DATA_DIR), FakeExchange())
        service.confirm_scheme("set-01", "c1", "2026-09-05")
        service.confirm_scheme("set-01", "c2", "2026-09-05")
        original = FakeExchange()
        original.submit({
            "operation_id": "op-r2", "action": "reserve", "supplier": "press-1",
            "classroom": "k2", "edition": "vol-a", "quantity": 18,
        })
        service.procurement.exchange = _UnknownExchange(original.receipts)
        service.procurement.reserve(
            operation_id="op-r2", supplier="press-1",
            classroom_id="k2", edition="vol-a", quantity=18, day="2026-09-06",
        )
        view = service.procurement.adjust(
            operation_id="op-adj", supplier="press-1",
            classroom_id="k2", edition="vol-a", quantity=0,
            reserve_operation_id="op-r2", day="2026-09-20",
        )
        # 减单结果未知，18 册继续占用，不能靠重算消失
        self.assertEqual(view["effective_quantity"], 18)
        status = service.order_status("k2", "2026-09-20")
        self.assertEqual(status["pending_adjustments"][0]["quantity_on_hold"], 18)
        # 合作方随后确认，轮询后才真正减为零
        service.procurement.exchange.known = True
        polled = service.procurement.poll_adjustment("op-adj")
        self.assertEqual(polled["state"], "completed")
        self.assertEqual(service.order_status("k2", "2026-09-20")["effective_quantity"], 0)

    def test_fallback_only_covers_uncommitted_part(self):
        self._bind_set01()
        self.service.procurement.reserve(
            operation_id="op-r", supplier="press-1",
            classroom_id="k2", edition="vol-a", quantity=18, day="2026-09-06",
        )
        # 没有县教务后备转用决定时不能自行后备出单
        with self.assertRaises(Uncommittable):
            self.service.procurement.reserve_fallback(
                operation_id="op-f", supplier="press-1",
                classroom_id="k2", quantity=1, day="2026-09-10",
            )
        # 即便有转用决定，已全部覆盖的班也一册后备都不能出，避免同班两套书
        self.service.state.transfers["k2"] = {
            "classroom": "k2", "book": "vol-c",
            "day": "2026-09-09", "reason": "尝试后备",
        }
        with self.assertRaises(ValidationError):
            self.service.procurement.reserve_fallback(
                operation_id="op-f", supplier="press-1",
                classroom_id="k2", quantity=1, day="2026-09-10",
            )
        # 部分覆盖的班：先由县教务转后备，后备只能出剩余册数
        self.service.procurement.reserve(
            operation_id="op-r3", supplier="press-1",
            classroom_id="k3", edition="vol-a", quantity=7, day="2026-09-06",
        )
        # k3 已开课且已有承诺，后备只覆盖未承诺部分（同书种承诺的余量换后备书种）
        self.service.state.transfers["k3"] = {
            "classroom": "k3", "book": "vol-c",
            "day": "2026-09-09", "reason": "部分册数转后备",
        }
        view = self.service.procurement.reserve_fallback(
            operation_id="op-f2", supplier="press-1",
            classroom_id="k3", quantity=5, day="2026-09-10",
        )
        # 正式承诺与后备承诺分行保存，合计不超过班级册数，避免同班两套满额书
        self.assertEqual(view["effective_quantity"], 7)
        self.assertEqual(view["fallback_quantity"], 5)

    def test_disconnect_after_accept_is_recovered_by_operation_id(self):
        service = SelectionService(
            Catalog.load(DATA_DIR), FakeExchange(disconnect_once=True)
        )
        service.confirm_scheme("set-01", "c1", "2026-09-05")
        service.confirm_scheme("set-01", "c2", "2026-09-05")
        view = service.procurement.reserve(
            operation_id="op-disc", supplier="press-1",
            classroom_id="k1", edition="vol-a", quantity=25, day="2026-09-06",
        )
        # 断连后内部已用原操作号轮询并拿到完成回执
        self.assertEqual(view["commitment"]["state"], "completed")
        self.assertEqual(view["commitment"]["receipt_id"], "demo-op-disc")

    def test_order_status_reports_effective_quantity_and_reasons(self):
        self._bind_set01()
        self.service.procurement.reserve(
            operation_id="op-r", supplier="press-1",
            classroom_id="k1", edition="vol-a", quantity=25, day="2026-09-06",
        )
        status = self.service.order_status("k1", "2026-09-20")
        self.assertEqual(status["effective_quantity"], 25)
        self.assertEqual(status["book"], "vol-a")
        self.assertEqual(status["selection"]["committable_book"], "vol-a")


class _UnknownExchange:
    """先返回未知结果，翻转 known 后按伙伴语义给出完成回执。"""

    def __init__(self, receipts):
        self.receipts = receipts
        self.known = False

    def submit(self, body):
        self.receipts[body["operation_id"]] = dict(body)
        return True, {"transport_error": "unknown"}

    def poll(self, operation_id):
        if not self.known or operation_id not in self.receipts:
            return None
        return {
            "operation_id": operation_id,
            "state": "completed",
            "receipt_id": "demo-" + operation_id,
            "request": self.receipts[operation_id],
        }
