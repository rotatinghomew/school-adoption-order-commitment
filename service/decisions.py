"""班级终选、期中切换、冲突裁决与后备转用。

本模块回答两个问题：

1. 某班某日**有权按哪本书开课**（authorized_book）——由生效授权决定；
2. 某班某日**实际按哪本书开课**（in_use_book）——已开教学班保留原书，
   直到县教务确认到可切换课次。

两者分离正是“调动还在核定的班级能解释当前依据及后续待核范围、
不强行算进两边、也不从订货清单消失”的落点。
"""

from .districts import (
    active_centers,
    approved_exceptions,
    explain_membership,
)
from .errors import Uncommittable, ValidationError
from .schemes import binding_schemes_for
from .timeaxis import within


# 决定种类，便于页面解释同一片区为何出现两种处理
KIND_EXCEPTION = "site_exception"          # 个别例外优先
KIND_SCHEME = "joint_scheme"               # 有效共同方案
KIND_ARBITRATION = "county_arbitration"    # 县教务裁决
KIND_CONTINUATION = "continued_original"   # 已开课按原决定继续
KIND_FALLBACK = "fallback_transfer"        # 转后备选用


def _book_available(catalog, state, book_id, day):
    book = catalog.choice(book_id)
    if not within(day, book["begin"], book["end"]):
        return False
    suspension = state.suspensions.get(book_id)
    return not (suspension is not None and suspension["suspended_on"] <= day)


def _class_started(catalog, classroom_id, day):
    return catalog.classroom(classroom_id)["starts"] <= day


def lesson_reached(classroom, day, switch_lesson):
    """课次是否已到达。

    资料只给出开课日与可切换课次，没有逐日课表；这里按开课日起每自然周
    一个课次估算当前课次（切换课次本身是“最早可切换课次”，用同一口径
    判断即可），县教务确认的课次到达后允许换书。
    """
    from .timeaxis import parse_day

    starts = parse_day(classroom["starts"])
    current = parse_day(day)
    current_lesson = (current - starts).days // 7 + 1
    return current_lesson >= switch_lesson


def _record_decision(state, classroom_id, day, kind, book_id, reason, extra=None):
    entry = {
        "decided_on": day,
        "kind": kind,
        "book": book_id,
        "reason": reason,
    }
    if extra:
        entry.update(extra)
    state.record_decision(classroom_id, entry)
    return entry


def arbitrate(catalog, state, classroom_id, book_id, day, reason):
    """县教务对两个冲突共同选择作裁决；裁决前不得承诺冲突订货。"""
    classroom = catalog.classroom(classroom_id)
    book = catalog.choice(book_id)
    if book["subject"] != classroom["subject"]:
        raise ValidationError("裁决书种与班级科目不符")
    if not _book_available(catalog, state, book_id, day):
        raise ValidationError("裁决书种已暂停或不在有效期")
    if not reason or not reason.strip():
        raise ValidationError("裁决必须附带理由")
    state.arbitrations[classroom_id] = {
        "classroom": classroom_id,
        "book": book_id,
        "day": day,
        "reason": reason.strip(),
    }
    _record_decision(state, classroom_id, day, KIND_ARBITRATION, book_id, reason.strip())
    return resolve(catalog, state, classroom_id, day)


def confirm_switch(catalog, state, classroom_id, book_id, day, lesson):
    """县教务确认切换课次；不得早于班级声明的最早可切换课次。"""
    classroom = catalog.classroom(classroom_id)
    earliest = classroom["switch_lesson"]
    if earliest is None:
        raise ValidationError("该班本学期未声明可切换课次，不能确认切换")
    if not isinstance(lesson, int) or lesson < earliest:
        raise ValidationError(
            f"切换课次不得早于最早可切换课次 {earliest}",
            details={"earliest_switch_lesson": earliest, "requested_lesson": lesson},
        )
    if book_id not in approved_exceptions(catalog, classroom_id, day, state):
        raise ValidationError("切换目标缺少该班已生效的个别授权")
    state.switches[(classroom_id, book_id)] = {
        "classroom": classroom_id,
        "book": book_id,
        "day": day,
        "lesson": lesson,
    }
    return resolve(catalog, state, classroom_id, day)


def earrest_text(lesson):
    return str(lesson)


def register_pending_move(catalog, state, classroom_id, from_center, to_center,
                          not_before, reason):
    """登记期中调动待核；不改动现有归属，核定前不算进两边。"""
    if not reason or not reason.strip():
        raise ValidationError("调动待核必须附带原因")
    state.pending_moves[classroom_id] = {
        "classroom": classroom_id,
        "from_center": from_center,
        "to_center": to_center,
        "not_before": not_before,
        "reason": reason.strip(),
    }
    return explain_membership(catalog, state, classroom_id, not_before)


def transfer_to_fallback(catalog, state, classroom_id, day, reason):
    """未对外承诺且未开课的班转后备选用；已开课班只能按原决定继续。"""
    classroom = catalog.classroom(classroom_id)
    if _class_started(catalog, classroom_id, day):
        raise Uncommittable(
            "班级已开始教学，不能转后备，按原决定继续",
            details={"classroom": classroom_id, "starts": classroom["starts"]},
        )
    if state.commitment_for(classroom_id) is not None:
        raise Uncommittable(
            "班级已有订货承诺，未被有效承诺覆盖的部分才允许后备出单",
            details={"classroom": classroom_id},
        )
    fallback = catalog.fallback_choice(classroom["subject"])
    if fallback is None or not _book_available(catalog, state, fallback["id"], day):
        raise Uncommittable("没有可用的后备书目")
    state.transfers[classroom_id] = {
        "classroom": classroom_id,
        "book": fallback["id"],
        "day": day,
        "reason": reason or "低于共同方案条件，县教务转后备选用",
    }
    _record_decision(
        state, classroom_id, day, KIND_FALLBACK, fallback["id"],
        state.transfers[classroom_id]["reason"],
    )
    return resolve(catalog, state, classroom_id, day)


def suspend_book(state, book_id, day, reason):
    """登记书目暂停；暂停不抹掉已承诺册数，只拦截新选择。"""
    if not reason or not reason.strip():
        raise ValidationError("暂停必须附带原因")
    state.suspensions[book_id] = {
        "book": book_id,
        "suspended_on": day,
        "reason": reason.strip(),
    }


def _authorized_book(catalog, state, classroom_id, day):
    """确定有权开课的书种与依据；冲突未裁时返回 conflict 视图。"""
    approved = approved_exceptions(catalog, classroom_id, day, state)
    if len(approved) == 1:
        book_id = next(iter(approved))
        return {"book": book_id, "kind": KIND_EXCEPTION,
                "basis_id": approved[book_id]["id"], "reason": "已生效个别授权优先"}
    if len(approved) > 1:
        return {"book": None, "kind": "conflicting_exceptions",
                "books": sorted(approved), "reason": "同一班存在多个生效个别授权，待县教务裁决"}

    views = binding_schemes_for(catalog, state, classroom_id, day)
    books = sorted({view["book"] for view in views})
    if len(books) == 1:
        return {"book": books[0], "kind": KIND_SCHEME,
                "schemes": [view["scheme"] for view in views],
                "reason": "有效共同方案"}
    if len(books) > 1:
        arbitration = state.arbitrations.get(classroom_id)
        if arbitration is not None and arbitration["day"] <= day and arbitration["book"] in books:
            return {"book": arbitration["book"], "kind": KIND_ARBITRATION,
                    "reason": arbitration["reason"], "arbitrated_on": arbitration["day"]}
        return {"book": None, "kind": "conflicting_schemes",
                "books": books,
                "schemes": [view["scheme"] for view in views],
                "reason": "两个有效共同选择冲突，县教务裁决前不得承诺"}

    arbitration = state.arbitrations.get(classroom_id)
    if arbitration is not None and arbitration["day"] <= day:
        return {"book": arbitration["book"], "kind": KIND_ARBITRATION,
                "reason": arbitration["reason"], "arbitrated_on": arbitration["day"]}

    transfer = state.transfers.get(classroom_id)
    if transfer is not None and transfer["day"] <= day:
        return {"book": transfer["book"], "kind": KIND_FALLBACK,
                "reason": transfer["reason"], "transferred_on": transfer["day"]}
    return {"book": None, "kind": "no_authorized_choice",
            "reason": "当日无生效授权：等待确认、裁决或后备转用"}


def resolve(catalog, state, classroom_id, day):
    """学校端查询：该班当前可按什么书开课，以及后续待核范围。"""
    classroom = catalog.classroom(classroom_id)
    authorized = _authorized_book(catalog, state, classroom_id, day)
    started = _class_started(catalog, classroom_id, day)
    latest = state.latest_decision(classroom_id, day)
    in_use = None
    in_use_kind = None
    pending_reason = None

    if authorized["book"] is not None:
        available = _book_available(catalog, state, authorized["book"], day)
        if not started:
            # 未开课：直接按当前授权准备开课，并把决定留痕
            if latest is None or latest["book"] != authorized["book"]:
                _record_decision(
                    state, classroom_id, day, authorized["kind"],
                    authorized["book"], authorized["reason"],
                )
            in_use = authorized["book"] if available else None
            in_use_kind = authorized["kind"] if available else None
            if not available:
                pending_reason = "授权书种已暂停，未确认一方不得抢占该班选择"
        elif latest is None:
            # 已开课但首次形成决定：按授权书种开课并留痕
            _record_decision(
                state, classroom_id, day, authorized["kind"],
                authorized["book"], authorized["reason"],
            )
            in_use = authorized["book"] if available else None
            in_use_kind = authorized["kind"] if available else None
        elif latest["book"] == authorized["book"]:
            in_use = latest["book"]
            in_use_kind = latest["kind"]
        else:
            # 授权书种变化且班级已开课：原书延续，等待切换课次
            switch = state.switches.get((classroom_id, authorized["book"]))
            if switch is not None and lesson_reached(classroom, day, switch["lesson"]):
                # 已到县教务确认的切换课次：正式改用新授权书种
                _record_decision(
                    state, classroom_id, day, authorized["kind"],
                    authorized["book"],
                    f"已到第 {switch['lesson']} 课次，按确认切换",
                    {"switch_lesson": switch["lesson"]},
                )
                in_use = authorized["book"]
                in_use_kind = authorized["kind"]
                pending_reason = None
            else:
                in_use = latest["book"]
                in_use_kind = KIND_CONTINUATION
                if switch is not None:
                    pending_reason = (
                        f"原书沿用至第 {switch['lesson']} 课次，之后切换为 "
                        f"{authorized['book']}"
                    )
                else:
                    pending_reason = (
                        f"新授权 {authorized['book']} 待县教务确认切换课次，"
                        "当前按原决定继续"
                    )
    elif started and latest is not None:
        # 授权暂时落空（书目暂停、方案失效等）：已开课班按原决定继续
        in_use = latest["book"]
        in_use_kind = KIND_CONTINUATION
        pending_reason = authorized["reason"]
    elif authorized["book"] is None:
        # 未开课且暂无授权：保留解释，班级不从清单消失
        pending_reason = authorized["reason"]

    # 可报订书种只看当前生效授权且书种可用；延续中的原书不再产生新承诺
    committable_book = None
    if authorized["book"] is not None and _book_available(
        catalog, state, authorized["book"], day
    ):
        committable_book = authorized["book"]
    elif authorized["book"] is not None:
        suspension_reason = "授权书种已暂停，未确认一方不得抢占该班选择"
        pending_reason = suspension_reason if pending_reason is None else (
            pending_reason + "；" + suspension_reason
        )

    membership = explain_membership(catalog, state, classroom_id, day)
    return {
        "classroom": classroom_id,
        "site": classroom["site"],
        "subject": classroom["subject"],
        "day": day,
        "started": started,
        "starts": classroom["starts"],
        "switch_lesson": classroom["switch_lesson"],
        "active_centers": active_centers(catalog, classroom_id, day),
        "authorized_book": authorized["book"],
        "authorized_kind": authorized["kind"],
        "authorized_detail": authorized,
        "in_use_book": in_use,
        "in_use_kind": in_use_kind,
        "committable_book": committable_book,
        "pending_reason": pending_reason,
        "membership": membership,
        "commitment": (
            state.commitment_for(classroom_id).to_dict()
            if state.commitment_for(classroom_id) is not None else None
        ),
    }


def require_committable(catalog, state, classroom_id, day):
    """采购报订前的唯一终选校验。"""
    view = resolve(catalog, state, classroom_id, day)
    if view["committable_book"] is None:
        raise Uncommittable(
            "该班当前没有可报订的唯一终选",
            details={
                "classroom": classroom_id,
                "authorized_kind": view["authorized_kind"],
                "pending_reason": view["pending_reason"],
            },
        )
    return view
