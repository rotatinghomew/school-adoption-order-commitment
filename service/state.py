"""运行期可变状态。

目录（:class:`~service.repository.Catalog`）保存县教务接口给出的静态事实；
本模块保存服务运行过程中产生、且不能靠重算静态清单恢复的记录：

申报、共同方案的确认与异议、县教务裁决、期中调动待核、切换确认、
书目暂停、后备转用、合作方订货操作与承诺行。

承诺行与合作方回执独立保存，这是“已承诺册数不能靠重算清单消失”的
落地方式：任何重算都只改资格视图，不改写已经签收的承诺。
"""

from dataclasses import dataclass, field

from .errors import IdempotencyConflict, ValidationError


@dataclass
class Commitment:
    """一个班在一个书种上的订货承诺行。"""

    classroom: str
    book: str
    quantity: int
    operation_id: str
    supplier: str
    state: str = "accepted"  # accepted / completed / adjustment_pending
    receipt_id: str | None = None
    history: list = field(default_factory=list)

    def to_dict(self):
        return {
            "classroom": self.classroom,
            "book": self.book,
            "quantity": self.quantity,
            "operation_id": self.operation_id,
            "supplier": self.supplier,
            "state": self.state,
            "receipt_id": self.receipt_id,
            "history": list(self.history),
        }


@dataclass
class SelectionState:
    """服务的全部运行期记录。"""

    applications: dict = field(default_factory=dict)          # id -> 申报
    runtime_exceptions: dict = field(default_factory=dict)    # id -> 运行期纳入的例外
    confirmations: dict = field(default_factory=dict)        # (scheme, center) -> 记录
    objections: dict = field(default_factory=dict)           # (scheme, center) -> 记录
    arbitrations: dict = field(default_factory=dict)         # classroom -> 裁决
    pending_moves: dict = field(default_factory=dict)        # classroom -> 调动待核
    switches: dict = field(default_factory=dict)             # (classroom, book) -> 切换确认
    suspensions: dict = field(default_factory=dict)          # book -> 暂停记录
    transfers: dict = field(default_factory=dict)            # classroom -> 后备转用
    decisions: dict = field(default_factory=dict)            # classroom -> 历次决定
    operations: dict = field(default_factory=dict)           # operation_id -> 受理记录
    commitments: dict = field(default_factory=dict)          # classroom -> Commitment（正式）
    fallback_commitments: dict = field(default_factory=dict)  # classroom -> Commitment（后备）

    # -- 幂等登记 -------------------------------------------------------

    def register_operation(self, operation_id, record):
        """登记操作号；同号内容不一致按交换契约回 409。"""
        previous = self.operations.get(operation_id)
        if previous is not None and previous != record:
            raise IdempotencyConflict(
                "相同操作号关联了不同内容",
                details={"operation_id": operation_id},
            )
        self.operations[operation_id] = record
        return previous is not None

    def record_decision(self, classroom_id, entry):
        """追加一条班级决定，同书种同窗不重复堆叠。"""
        entries = self.decisions.setdefault(classroom_id, [])
        entries.append(entry)

    def latest_decision(self, classroom_id, day):
        """取某日（含）之前最近一条仍有依据意义的决定。"""
        result = None
        for entry in self.decisions.get(classroom_id, []):
            if entry["decided_on"] <= day:
                result = entry
        return result

    # -- 承诺行 ---------------------------------------------------------

    def commitment_for(self, classroom_id):
        return self.commitments.get(classroom_id)

    def put_commitment(self, commitment: Commitment):
        existing = self.commitments.get(commitment.classroom)
        if existing is not None and existing.book != commitment.book:
            raise ValidationError(
                "同一班同一科目只能按一个最终选择报订",
                details={
                    "classroom": commitment.classroom,
                    "existing_book": existing.book,
                    "requested_book": commitment.book,
                },
            )
        self.commitments[commitment.classroom] = commitment
