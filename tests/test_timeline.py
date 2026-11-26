"""按合成资料的真实时间线做端到端业务演练。"""

from service import FakeExchange

from tests.support import ServiceTestCase


class TimelineTests(ServiceTestCase):
    def test_full_term_timeline_matches_domain_narrative(self):
        # 学期初：两所中心校确认共同方案 set-01
        self.service.confirm_scheme("set-01", "c1", "2026-09-05")
        self.service.confirm_scheme("set-01", "c2", "2026-09-05")
        k1 = self.service.resolve_classroom("k1", "2026-09-06")
        k2 = self.service.resolve_classroom("k2", "2026-09-06")
        k3 = self.service.resolve_classroom("k3", "2026-09-06")
        self.assertEqual(k1["committable_book"], "vol-a")
        self.assertEqual(k2["in_use_book"], "vol-a")
        self.assertEqual(k3["in_use_book"], "vol-a")
        # 共建班 k2 同时在两所中心校片区内，但只计一次
        scope = self.service.scheme_scope("set-01", "2026-09-06")
        self.assertEqual(scope["distinct_classes"], 3)

        # 09-20：set-02 的 c3 片区生效，两方案对 k2/k3 指向不同书种 -> 冲突
        self.service.confirm_scheme("set-02", "c2", "2026-09-20")
        self.service.confirm_scheme("set-02", "c3", "2026-09-20")
        conflict = self.service.resolve_classroom("k2", "2026-09-21")
        self.assertIsNone(conflict["committable_book"])
        self.assertEqual(conflict["authorized_kind"], "conflicting_schemes")

        # 09-25：k2 的 vol-b 个别例外审核准许并生效
        view = self.service.resolve_classroom("k2", "2026-09-25")
        self.assertEqual(view["authorized_book"], "vol-b")
        # k2 已开课，原书 vol-a 延续，等待切换课次
        self.assertEqual(view["in_use_book"], "vol-a")
        self.assertEqual(view["in_use_kind"], "continued_original")
        # 例外改变剩余班级的方案资格：set-01 只剩 2 个班，低于 minimum=3
        self.assertFalse(self.service.scheme_scope("set-01", "2026-09-25")["binding"])
        # set-02 同样只剩 k3、k4，低于门槛，旧确认变为过期确认
        set02 = self.service.scheme_scope("set-02", "2026-09-25")
        self.assertFalse(set02["binding"])
        self.assertIn("c2", set02["stale_confirmations"])

        # 已开课的 k1、k3 按原决定继续，页面能解释依据
        for classroom_id in ("k1", "k3"):
            continued = self.service.resolve_classroom(classroom_id, "2026-09-26")
            self.assertEqual(continued["in_use_book"], "vol-a")
            self.assertTrue(continued["pending_reason"] or continued["in_use_kind"])

        # k2 确认第 12 课次切换，课次到达后换书
        self.service.confirm_switch("k2", "vol-b", "2026-09-26", 12)
        before = self.service.resolve_classroom("k2", "2026-10-10")
        self.assertEqual(before["in_use_book"], "vol-a")
        after = self.service.resolve_classroom("k2", "2026-11-27")
        self.assertEqual(after["in_use_book"], "vol-b")

        # 未开课的 k4 在门槛失效后可由县教务转后备
        fallback = self.service.transfer_to_fallback("k4", "2026-10-01", "方案低于条件")
        self.assertEqual(fallback["authorized_book"], "vol-c")

    def test_classroom_without_any_choice_is_explained_not_dropped(self):
        # k4 在 09-20 片区生效前既无方案资格也无承诺，不会从清单消失
        view = self.service.resolve_classroom("k4", "2026-09-10")
        self.assertIsNone(view["in_use_book"])
        self.assertEqual(view["authorized_kind"], "no_authorized_choice")
        self.assertTrue(view["pending_reason"])
        status = self.service.order_status("k4", "2026-09-10")
        self.assertEqual(status["effective_quantity"], 0)
