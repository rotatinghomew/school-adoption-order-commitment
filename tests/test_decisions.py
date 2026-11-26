"""班级终选、冲突裁决、原书延续、切换与后备转用。"""

from service import Uncommittable, ValidationError

from tests.support import ServiceTestCase


class DecisionTests(ServiceTestCase):
    def test_conflicting_schemes_block_commitment_until_arbitration(self):
        self.confirm_both("set-01", "2026-09-05")
        self.confirm_both("set-02", "2026-09-20")
        view = self.service.resolve_classroom("k2", "2026-09-21")
        self.assertIsNone(view["committable_book"])
        self.assertEqual(view["authorized_kind"], "conflicting_schemes")
        self.assertEqual(view["authorized_detail"]["books"], ["vol-a", "vol-b"])

        self.service.arbitrate(
            "k2", "vol-a", "2026-09-22", "按片区多数班级教学连续性裁决沿用 vol-a"
        )
        view = self.service.resolve_classroom("k2", "2026-09-22")
        self.assertEqual(view["authorized_book"], "vol-a")
        self.assertEqual(view["authorized_kind"], "county_arbitration")
        self.assertEqual(view["committable_book"], "vol-a")

    def test_approved_exception_rebases_authorized_book_but_started_class_continues(self):
        # 先让 set-01 对 k2 形成有效决定并开课
        self.confirm_both("set-01", "2026-09-05")
        view = self.service.resolve_classroom("k2", "2026-09-05")
        self.assertEqual(view["in_use_book"], "vol-a")
        # 09-25 例外准许 vol-b：授权变化，但 k2 已开课，原书延续
        view = self.service.resolve_classroom("k2", "2026-09-25")
        self.assertEqual(view["authorized_book"], "vol-b")
        self.assertEqual(view["in_use_book"], "vol-a")
        self.assertEqual(view["in_use_kind"], "continued_original")
        self.assertIn("待县教务确认切换课次", view["pending_reason"])

    def test_switch_requires_declared_lesson_and_then_changes_in_use_book(self):
        self.confirm_both("set-01", "2026-09-05")
        self.service.resolve_classroom("k2", "2026-09-05")
        with self.assertRaises(ValidationError):
            # k2 最早可切换课次是 12
            self.service.confirm_switch("k2", "vol-b", "2026-09-26", 10)
        self.service.confirm_switch("k2", "vol-b", "2026-09-26", 12)
        view = self.service.resolve_classroom("k2", "2026-09-26")
        self.assertEqual(view["authorized_book"], "vol-b")
        # 第 12 课次尚未到达（k2 09-04 开课，09-26 仅第 4 课），原书 vol-a 延续
        self.assertEqual(view["in_use_book"], "vol-a")
        self.assertEqual(view["in_use_kind"], "continued_original")
        self.assertIn("第 12 课次", view["pending_reason"])
        # 到达第 12 课次（开课约 11 周后）后正式换用 vol-b
        reached = self.service.resolve_classroom("k2", "2026-11-27")
        self.assertEqual(reached["in_use_book"], "vol-b")
        self.assertIsNone(reached["pending_reason"])

    def test_classroom_without_switch_lesson_keeps_book_all_term(self):
        # k3 的 switch_lesson 为 null
        self.confirm_both("set-01", "2026-09-05")
        self.service.resolve_classroom("k3", "2026-09-05")
        self.service.intake_exception({
            "id": "rt-k3", "classroom": "k3", "book": "vol-b",
            "decision": "审核准许", "effective": "2026-10-08",
        })
        with self.assertRaises(ValidationError):
            self.service.confirm_switch("k3", "vol-b", "2026-10-09", 5)
        view = self.service.resolve_classroom("k3", "2026-10-09")
        self.assertEqual(view["authorized_book"], "vol-b")
        self.assertEqual(view["in_use_book"], "vol-a")
        self.assertEqual(view["in_use_kind"], "continued_original")

    def test_fallback_for_unstarted_class_but_continuation_for_started(self):
        # set-01 先有效，随后例外使范围低于门槛
        self.confirm_both("set-01", "2026-09-05")
        self.service.resolve_classroom("k1", "2026-09-05")  # k1 09-03 已开课
        # 制造门槛失效：让 set-01 范围缩小到低于 minimum
        self.service.suspend_book("vol-a", "2026-09-20", "暂停供货")
        # 已开课的 k1 不允许转后备，只能按原决定继续
        with self.assertRaises(Uncommittable):
            self.service.transfer_to_fallback("k1", "2026-09-20", "方案失效")
        view = self.service.resolve_classroom("k1", "2026-09-20")
        self.assertEqual(view["in_use_book"], "vol-a")
        self.assertEqual(view["in_use_kind"], "continued_original")
        # k4 10-10 才开课；在开课前转后备是允许的
        view = self.service.transfer_to_fallback("k4", "2026-10-01", "共同方案低于条件")
        self.assertEqual(view["authorized_book"], "vol-c")
        self.assertEqual(view["authorized_kind"], "fallback_transfer")

    def test_suspended_authorized_book_is_not_committable(self):
        self.confirm_both("set-01", "2026-09-05")
        self.service.resolve_classroom("k1", "2026-09-05")
        self.service.suspend_book("vol-a", "2026-09-08", "书目暂停")
        view = self.service.resolve_classroom("k1", "2026-09-08")
        # 书种暂停使共同方案失去约束力：没有可报订书种，已开课班原书延续
        self.assertIsNone(view["committable_book"])
        self.assertEqual(view["in_use_book"], "vol-a")
        self.assertEqual(view["in_use_kind"], "continued_original")
        self.assertTrue(view["pending_reason"])

    def test_membership_explains_two_treatments_in_one_district(self):
        # 同一片区：已开课 k1 延续原书，未开课 k4 可转后备
        self.confirm_both("set-01", "2026-09-05")
        self.service.resolve_classroom("k1", "2026-09-05")
        self.service.suspend_book("vol-a", "2026-09-20", "暂停")
        continued = self.service.resolve_classroom("k1", "2026-09-21")
        fallback = self.service.transfer_to_fallback("k4", "2026-09-21", "门槛失效")
        self.assertEqual(continued["in_use_kind"], "continued_original")
        self.assertEqual(fallback["authorized_kind"], "fallback_transfer")
        # 页面可分别解释原因
        self.assertTrue(continued["pending_reason"])
        self.assertTrue(fallback["authorized_detail"]["reason"])
