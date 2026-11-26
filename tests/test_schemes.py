"""共同方案的适用范围、确认门槛、异议与例外影响。"""

from service import ValidationError

from tests.support import ServiceTestCase


class SchemeScopeTests(ServiceTestCase):
    def test_scope_deduplicates_classes_and_lists_exclusions(self):
        view = self.scope("set-01", "2026-09-10")
        # k2 同时属于 c1/c2，方案班级列一次即可，不重复抬高口径
        self.assertEqual(view["included_classes"], ["k1", "k2", "k3"])
        self.assertEqual(view["distinct_classes"], 3)

    def test_binding_requires_confirmations_on_current_scope(self):
        view = self.scope("set-01", "2026-09-10")
        self.assertFalse(view["binding"])
        self.confirm_both("set-01", "2026-09-10")
        view = self.scope("set-01", "2026-09-10")
        self.assertTrue(view["binding"])
        self.assertEqual(view["confirming_centers"], ["c1", "c2"])

    def test_objection_with_reason_voids_that_center_confirmation(self):
        self.confirm_both("set-01", "2026-09-10")
        self.service.object_scheme(
            "set-01", "c2", "2026-09-11", "k2 的共建归属正在复核，范围有异议"
        )
        view = self.scope("set-01", "2026-09-11")
        self.assertIn("c2", view["objecting_centers"])
        self.assertFalse(view["binding"])
        with self.assertRaises(ValidationError):
            self.service.object_scheme("set-01", "c2", "2026-09-11", "  ")

    def test_old_confirmation_cannot_decide_after_scope_changes(self):
        # k4 的归属 09-20 才生效，set-02 在 09-20 后满足 3 个班、2 所中心
        self.confirm_both("set-02", "2026-09-20")
        self.assertTrue(self.scope("set-02", "2026-09-20")["binding"])
        # k2 的个别例外在 09-25 生效，范围签名变化
        view = self.scope("set-02", "2026-09-25")
        self.assertIn("k2", view["excluded_classes"])
        self.assertIn("c2", view["stale_confirmations"])
        self.assertFalse(view["binding"])

    def test_below_minimum_is_not_binding_and_reconfirm_restores(self):
        self.confirm_both("set-02", "2026-09-20")
        # 09-25 后 k2 被例外剔除：set-02 剩 k3、k4 两个班，低于 minimum=3
        view = self.scope("set-02", "2026-09-25")
        self.assertEqual(view["distinct_classes"], 2)
        self.assertFalse(view["binding"])
        # 重新确认只反映当前范围，不会让旧确认复活
        for center in self.service.catalog.scheme("set-02")["centers"]:
            self.service.confirm_scheme("set-02", center, "2026-09-26")
        view = self.scope("set-02", "2026-09-26")
        self.assertEqual(view["stale_confirmations"], [])
        self.assertFalse(view["binding"], "班级数仍低于门槛，确认也不能约束")

    def test_non_participant_center_cannot_confirm(self):
        with self.assertRaises(ValidationError):
            self.service.confirm_scheme("set-01", "c3", "2026-09-10")

    def test_suspension_removes_book_choices_from_scope(self):
        self.confirm_both("set-01", "2026-09-10")
        self.service.suspend_book("vol-a", "2026-09-15", "供货方书目暂停")
        view = self.scope("set-01", "2026-09-15")
        self.assertEqual(
            set(view["excluded_classes"]), {"k1", "k2", "k3"}
        )
        self.assertFalse(view["binding"])
