"""班级归属、共建片区与授权判断。

班级标识跨共建片区唯一；一个班可同时落在多条半开归属区间内，因此
可能处在两所中心校共建的片区里（如资料中的 k2）。期中班级调整通过
新的归属区间表达，已开课班级的原书保留由决定与切换模块处理，这里
只回答“某时归属谁、谁有生效授权、依据是什么”。
"""

from .errors import AuthorizationError
from .repository import APPROVED_DECISION
from .timeaxis import within


def active_districts(catalog, classroom_id, day):
    """某日对该班生效的归属区间（半开）。"""
    return [
        row
        for row in catalog.districts_for(classroom_id)
        if within(day, row["begin"], row["end"])
    ]


def active_centers(catalog, classroom_id, day):
    """某日对该班有归属关系的中心校，按编号去重并排序。"""
    return sorted({row["center"] for row in active_districts(catalog, classroom_id, day)})


def has_basis(catalog, classroom_id, center_id, day):
    """中心校在某日是否对该班有归属依据（含共建片区）。"""
    return center_id in active_centers(catalog, classroom_id, day)


def _exception_rows(catalog, state, classroom_id):
    """静态资料与运行期纳入的例外合并，运行期记录编号不得与资料冲突。"""
    rows = list(catalog.exceptions_for(classroom_id))
    rows.extend(
        row
        for row in state.runtime_exceptions.values()
        if row["classroom"] == classroom_id
    )
    return rows


def approved_exceptions(catalog, classroom_id, day, state=None):
    """已审核准许且在某日生效的个别授权，按书种去重保留最近一条。"""
    rows = (
        _exception_rows(catalog, state, classroom_id)
        if state is not None else catalog.exceptions_for(classroom_id)
    )
    approved = {}
    for row in rows:
        if row["decision"] != APPROVED_DECISION:
            continue
        if row["effective"] is not None and row["effective"] <= day:
            previous = approved.get(row["book"])
            if previous is None or row["effective"] > previous["effective"]:
                approved[row["book"]] = row
    return approved


def pending_exception_requests(catalog, classroom_id, day, state=None):
    """尚未形成生效授权的申请记录，用于解释“待核范围”。"""
    rows = (
        _exception_rows(catalog, state, classroom_id)
        if state is not None else catalog.exceptions_for(classroom_id)
    )
    result = []
    for row in rows:
        if row["decision"] == APPROVED_DECISION:
            if row["effective"] is not None and row["effective"] <= day:
                continue
        result.append(row)
    return result


def exception_parties(catalog, classroom_id, day, state=None):
    """因个别例外获得选择权的校点（以班级所在教学站点表达）。"""
    approved = approved_exceptions(catalog, classroom_id, day, state)
    if not approved:
        return []
    site = catalog.classroom(classroom_id)["site"]
    return [{"party": site, "kind": "site_exception", "books": sorted(approved)}]


def authorized_parties(catalog, state, classroom_id, day):
    """汇总某日可为该班作选择的全部依据。

    个别授权优先：已生效例外覆盖的书种，共建中心校不能再代选；
    共建中心校仍可对未被例外覆盖的部分按共同方案作选择。
    """
    centers = [
        {"party": center_id, "kind": "district_center", "books": None}
        for center_id in active_centers(catalog, classroom_id, day)
    ]
    exceptions = exception_parties(catalog, classroom_id, day, state)
    move = state.pending_moves.get(classroom_id)
    pending = []
    if move is not None and move["not_before"] <= day:
        pending.append(move)
    return {
        "classroom": classroom_id,
        "day": day,
        "active_centers": centers,
        "site_exceptions": exceptions,
        "pending_exception_requests": pending_exception_requests(
            catalog, classroom_id, day, state
        ),
        "pending_moves": pending,
    }


def require_center_basis(catalog, state, classroom_id, center_id, day):
    """中心校申报/确认前的授权校验，失败给出可执行的依据说明。"""
    if not has_basis(catalog, classroom_id, center_id, day):
        raise AuthorizationError(
            f"中心校 {center_id} 在 {day} 对班级 {classroom_id} 没有生效归属依据",
            details={
                "classroom": classroom_id,
                "center": center_id,
                "active_centers": active_centers(catalog, classroom_id, day),
            },
        )


def require_exception_basis(catalog, state, classroom_id, book_id, day):
    """校点自行选用前必须存在已生效的个别授权。"""
    approved = approved_exceptions(catalog, classroom_id, day, state)
    if book_id not in approved:
        raise AuthorizationError(
            f"班级 {classroom_id} 对书目 {book_id} 没有已生效的个别授权",
            details={
                "classroom": classroom_id,
                "book": book_id,
                "approved_books": sorted(approved),
                "pending_requests": pending_exception_requests(
                    catalog, classroom_id, day, state
                ),
            },
        )


def explain_membership(catalog, state, classroom_id, day):
    """页面解释：当前依据、共建方，以及调动待核的后续范围。"""
    rows = active_districts(catalog, classroom_id, day)
    basis = [
        {
            "district": row["id"],
            "center": row["center"],
            "window": [row["begin"], row["end"]],
        }
        for row in rows
    ]
    move = state.pending_moves.get(classroom_id)
    return {
        "classroom": classroom_id,
        "day": day,
        "current_basis": basis,
        "co_building": len({row["center"] for row in rows}) > 1,
        "approved_exceptions": sorted(
            approved_exceptions(catalog, classroom_id, day, state)
        ),
        "pending_moves": [move] if move is not None and move["not_before"] <= day else [],
    }
