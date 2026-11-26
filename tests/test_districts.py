"""片区归属、共建片区与授权优先级规则。"""

from service import AuthorizationError, ScopeConflict

from tests.support import ServiceTestCase


class DistrictTests(ServiceTestCase):
    def test_half_open_membership_ends_on_boundary(self):
        # district-2: c1 对 k2 的归属在 2026-10-01 当天失效（半开）
        self.assertEqual(
            self.service.membership("k2", "2026-09-30")["current_basis"][0]["center"],
            "c1",
        )
        view = self.service.membership("k2", "2026-10-01")
        centers = {item["center"] for item in view["current_basis"]}
        self.assertEqual(centers, {"c2"})

    def test_co_building_classroom_has_two_centers_before_transfer(self):
        parties = self.service.parties_for("k2", "2026-09-10")
        self.assertEqual(
            [item["party"] for item in parties["active_centers"]], ["c1", "c2"]
        )
        self.assertTrue(self.service.membership("k2", "2026-09-10")["co_building"])

    def test_pending_exception_request_has_no_authority_before_approval(self):
        parties = self.service.parties_for("k2", "2026-09-24")
        self.assertEqual(parties["site_exceptions"], [])
        pending_ids = {row["id"] for row in parties["pending_exception_requests"]}
        self.assertIn("exception-b", pending_ids)

    def test_approved_exception_overrides_co_building_choice(self):
        parties = self.service.parties_for("k2", "2026-09-25")
        self.assertEqual(
            parties["site_exceptions"][0]["books"], ["vol-b"]
        )
        # 生效日前一天仍无例外授权
        self.assertEqual(
            self.service.parties_for("k2", "2026-09-24")["site_exceptions"], []
        )

    def test_duplicate_application_for_same_class_is_rejected(self):
        base = {
            "id": "app-1", "center": "c1", "classroom": "k2",
            "subject": "地理", "book": "vol-a", "term": "2026秋", "day": "2026-09-05",
        }
        self.service.submit_application(base)
        duplicate = dict(base, id="app-2", center="c2", book="vol-b")
        with self.assertRaises(ScopeConflict):
            self.service.submit_application(duplicate)

    def test_center_without_basis_cannot_apply(self):
        # c3 对 k1 从未有过归属区间
        with self.assertRaises(AuthorizationError):
            self.service.submit_application({
                "id": "app-x", "center": "c3", "classroom": "k1",
                "subject": "地理", "book": "vol-a",
                "term": "2026秋", "day": "2026-09-05",
            })

    def test_pending_move_does_not_count_classroom_on_both_sides(self):
        self.service.register_pending_move(
            "k2", "c1", "c3", "2026-10-02", "片区调整，等待县教务核定"
        )
        view = self.service.membership("k2", "2026-10-02")
        # 现有归属未被改写，待核调动单独列出，不产生 c3 的生效依据
        self.assertEqual({b["center"] for b in view["current_basis"]}, {"c2"})
        self.assertEqual(view["pending_moves"][0]["to_center"], "c3")
        parties = self.service.parties_for("k2", "2026-10-02")
        self.assertNotIn("c3", [p["party"] for p in parties["active_centers"]])

    def test_runtime_exception_intake_joins_same_authority_pipeline(self):
        # 为 k4 纳入一条新的运行期准许记录，立即取得授权
        self.service.intake_exception({
            "id": "rt-1", "classroom": "k4", "book": "vol-b",
            "decision": "审核准许", "effective": "2026-10-15",
        })
        parties = self.service.parties_for("k4", "2026-10-15")
        self.assertEqual(parties["site_exceptions"][0]["books"], ["vol-b"])
        self.assertEqual(self.service.parties_for("k4", "2026-10-14")["site_exceptions"], [])
