"""选用后端门面：教务与采购共用同一个后端入口。

:class:`SelectionService` 组合只读目录、运行期状态与采购服务，
HTTP 层与测试都通过它操作，保证两侧看到同一份口径。
"""

from .decisions import (
    arbitrate,
    confirm_switch,
    register_pending_move,
    resolve,
    suspend_book,
    transfer_to_fallback,
)
from .districts import authorized_parties, explain_membership
from .intake import intake_exception
from .procurement import ProcurementService
from .schemes import (
    binding_schemes_for,
    confirm_scope,
    object_scope,
    scope_view,
    submit_application,
)
from .state import SelectionState


class SelectionService:
    """教务、采购共用的选用后端。"""

    def __init__(self, catalog, exchange=None):
        self.catalog = catalog
        self.state = SelectionState()
        self.procurement = ProcurementService(catalog, self.state, exchange)

    # -- 教务：申报与授权 ----------------------------------------------

    def submit_application(self, application):
        return submit_application(self.catalog, self.state, application)

    def parties_for(self, classroom_id, day):
        return authorized_parties(self.catalog, self.state, classroom_id, day)

    def intake_exception(self, record):
        return intake_exception(self.catalog, self.state, record)

    def membership(self, classroom_id, day):
        return explain_membership(self.catalog, self.state, classroom_id, day)

    # -- 教务：共同方案 -------------------------------------------------

    def scheme_scope(self, scheme_id, day):
        return scope_view(self.catalog, self.state, scheme_id, day)

    def confirm_scheme(self, scheme_id, center_id, day):
        return confirm_scope(self.catalog, self.state, scheme_id, center_id, day)

    def object_scheme(self, scheme_id, center_id, day, reason):
        return object_scope(
            self.catalog, self.state, scheme_id, center_id, day, reason
        )

    def binding_schemes(self, classroom_id, day):
        return binding_schemes_for(self.catalog, self.state, classroom_id, day)

    # -- 教务：例外、裁决、调动、切换 -----------------------------------

    def arbitrate(self, classroom_id, book_id, day, reason):
        return arbitrate(self.catalog, self.state, classroom_id, book_id, day, reason)

    def register_pending_move(self, classroom_id, from_center, to_center,
                              not_before, reason):
        return register_pending_move(
            self.catalog, self.state, classroom_id, from_center, to_center,
            not_before, reason,
        )

    def confirm_switch(self, classroom_id, book_id, day, lesson):
        return confirm_switch(
            self.catalog, self.state, classroom_id, book_id, day, lesson
        )

    def suspend_book(self, book_id, day, reason):
        return suspend_book(self.state, book_id, day, reason)

    def transfer_to_fallback(self, classroom_id, day, reason=None):
        return transfer_to_fallback(
            self.catalog, self.state, classroom_id, day, reason
        )

    # -- 学校与采购查询 -------------------------------------------------

    def resolve_classroom(self, classroom_id, day):
        return resolve(self.catalog, self.state, classroom_id, day)

    def order_status(self, classroom_id, day):
        return self.procurement.class_order_status(classroom_id, day)
