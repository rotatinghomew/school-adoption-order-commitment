"""运行期受理校点个别授权（申请与审核准许）。

静态资料中的例外来自县教务接口；服务运行中新产生的授权通过本模块
纳入同一份状态，立即参与授权优先级、方案资格与切换判断，保证
下一学期继续接收不同片区和课程的申报时口径不变。
"""

from .errors import ValidationError
from .repository import APPROVED_DECISION

ALLOWED_DECISIONS = {"申请改用", APPROVED_DECISION}


def intake_exception(catalog, state, record):
    """登记一条运行期例外记录。"""
    required = {"id", "classroom", "book", "decision", "effective"}
    missing = required - set(record)
    if missing:
        raise ValidationError(f"例外记录缺少字段: {sorted(missing)}")
    if record["decision"] not in ALLOWED_DECISIONS:
        raise ValidationError(
            f"decision 必须是 {sorted(ALLOWED_DECISIONS)} 之一",
            details={"decision": record["decision"]},
        )
    classroom = catalog.classroom(record["classroom"])
    book = catalog.choice(record["book"])
    if book["subject"] != classroom["subject"]:
        raise ValidationError("例外书种与班级科目不符")
    if record["id"] in state.runtime_exceptions:
        raise ValidationError(
            "例外编号已存在", details={"id": record["id"]}
        )
    state.runtime_exceptions[record["id"]] = dict(record)
    return state.runtime_exceptions[record["id"]]
