"""共同教学方案的适用范围、确认与约束力。

规则落点（对应题述与 README）：

* 适用范围先按半开时间轴去除失效归属、已生效个别例外和暂停书目，
  任一班只计一次，避免共建片区里重复申报抬高联合选用口径；
* ``minimum`` 是仍符合方案条件的不同班级数，``distinct_centers`` 是
  对当前范围作出有效确认的不同中心校数；
* 确认绑定范围签名；范围变化（含新增个别例外、书目暂停、调动）后
  旧确认成为过期确认，不能替对应中心校的班级作决定；
* 任一所中心校提出有理由的异议，在其重新确认前，它的确认不计入；
* 两个仍有效且无例外的共同选择对同一班指向不同书种时构成冲突，
  裁决前不得形成承诺（冲突的承诺拦截在 decisions/procurement 层）。
"""

import json

from .districts import (
    active_centers,
    approved_exceptions,
    require_center_basis,
)
from .errors import ScopeConflict, ValidationError
from .timeaxis import within


def _suspended(state, book_id, day):
    record = state.suspensions.get(book_id)
    return record is not None and record["suspended_on"] <= day


def eligible_classes(catalog, state, scheme, day):
    """计算某日方案的适用班级集合与排除原因。"""
    book = catalog.choice(scheme["book"])
    included, excluded = [], {}
    for classroom_id in scheme["classes"]:
        classroom = catalog.classroom(classroom_id)
        if classroom["subject"] != book["subject"]:
            excluded[classroom_id] = "科目与方案书种不一致"
            continue
        if not active_centers(catalog, classroom_id, day):
            excluded[classroom_id] = "当日无生效片区归属（调动待核或区间失效）"
            continue
        if approved_exceptions(catalog, classroom_id, day, state):
            excluded[classroom_id] = "已有生效个别授权，例外优先于共同选择"
            continue
        if not within(day, book["begin"], book["end"]):
            excluded[classroom_id] = "书种不在有效选书期内"
            continue
        if _suspended(state, book["id"], day):
            excluded[classroom_id] = "书种已暂停，等待处理"
            continue
        included.append(classroom_id)
    return sorted(set(included)), excluded


def scope_signature(scheme, included, book_suspended):
    """范围签名：纳入班级集合或书种状态一变，签名即变。"""
    payload = {
        "scheme": scheme["id"],
        "book": scheme["book"],
        "classes": included,
        "book_suspended": book_suspended,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def valid_confirmations(state, scheme, signature):
    """对当前签名仍有效的确认，排除异议未消与签名过期的中心校。"""
    result = []
    for center_id in scheme["centers"]:
        confirmation = state.confirmations.get((scheme["id"], center_id))
        objection = state.objections.get((scheme["id"], center_id))
        if confirmation is None or confirmation["signature"] != signature:
            continue
        if objection is not None and objection["day"] >= confirmation["day"]:
            continue
        result.append(center_id)
    return sorted(set(result))


def scope_view(catalog, state, scheme_id, day):
    """供参与中心校确认前查看的去重、去例外后的适用范围。"""
    scheme = catalog.scheme(scheme_id)
    included, excluded = eligible_classes(catalog, state, scheme, day)
    suspended = _suspended(state, scheme["book"], day)
    signature = scope_signature(scheme, included, suspended)
    confirming = valid_confirmations(state, scheme, signature)
    meets = (
        len(included) >= scheme["minimum"]
        and len(confirming) >= scheme["distinct_centers"]
    )
    stale = [
        center_id
        for center_id in scheme["centers"]
        if (scheme_id, center_id) in state.confirmations
        and state.confirmations[(scheme_id, center_id)]["signature"] != signature
    ]
    objecting = [
        center_id
        for center_id in scheme["centers"]
        if (scheme_id, center_id) in state.objections
        and state.objections[(scheme_id, center_id)]["day"]
        >= state.confirmations.get((scheme_id, center_id), {"day": ""})["day"]
    ]
    return {
        "scheme": scheme_id,
        "day": day,
        "book": scheme["book"],
        "subject": catalog.choice(scheme["book"])["subject"],
        "included_classes": included,
        "excluded_classes": excluded,
        "distinct_classes": len(included),
        "minimum_classes": scheme["minimum"],
        "confirming_centers": confirming,
        "stale_confirmations": sorted(set(stale)),
        "objecting_centers": sorted(set(objecting)),
        "distinct_centers": len(confirming),
        "required_centers": scheme["distinct_centers"],
        "binding": meets,
        "signature": signature,
    }


def submit_application(catalog, state, application):
    """登记中心校申报，拦截同班同科的重复申报。"""
    required = {"id", "center", "classroom", "subject", "book", "term", "day"}
    missing = required - set(application)
    if missing:
        raise ValidationError(f"申报缺少字段: {sorted(missing)}")
    classroom = catalog.classroom(application["classroom"])
    if classroom["subject"] != application["subject"]:
        raise ValidationError("申报科目与班级实际科目不符")
    book = catalog.choice(application["book"])
    if book["subject"] != application["subject"]:
        raise ValidationError("拟选用书种不属于该科目")
    if not within(application["day"], book["begin"], book["end"]):
        raise ValidationError("拟选用书种在申报日不在有效期内")
    require_center_basis(
        catalog, state, application["classroom"], application["center"], application["day"]
    )
    for existing in state.applications.values():
        if (
            existing["classroom"] == application["classroom"]
            and existing["subject"] == application["subject"]
            and existing["term"] == application["term"]
        ):
            if existing["id"] == application["id"]:
                # 同号重报按幂等处理，直接回原记录
                return existing
            raise ScopeConflict(
                "同一班同一科目在该学期已有申报，联合口径不得重复计入",
                details={
                    "classroom": application["classroom"],
                    "existing_application": existing["id"],
                    "existing_center": existing["center"],
                },
            )
    state.applications[application["id"]] = dict(application)
    return state.applications[application["id"]]


def confirm_scope(catalog, state, scheme_id, center_id, day):
    """中心校对当前适用范围作确认，确认记录绑定范围签名。

    参与中心校只需对当前纳入范围中至少一个班有生效归属依据：共同方案
    覆盖多个片区，任一所中心校都不可能对片区外的班有直接归属。
    """
    scheme = catalog.scheme(scheme_id)
    if center_id not in scheme["centers"]:
        raise ValidationError(f"中心校 {center_id} 不是方案 {scheme_id} 的参与方")
    # 参与资格以方案名册为准；共建片区可能在确认日之后才生效（如 k4 的
    # 归属 09-20 开始）。确认绑定的是范围签名，范围变化后旧确认自动失效，
    # 中心校不能据此替片区外或已变化的班级作决定。
    view = scope_view(catalog, state, scheme_id, day)
    state.confirmations[(scheme_id, center_id)] = {
        "scheme": scheme_id,
        "center": center_id,
        "day": day,
        "signature": view["signature"],
        "included_classes": view["included_classes"],
    }
    return scope_view(catalog, state, scheme_id, day)


def object_scope(catalog, state, scheme_id, center_id, day, reason):
    """有理由的异议：理由为空不受理，异议后旧确认不再替该中心作决定。"""
    scheme = catalog.scheme(scheme_id)
    if center_id not in scheme["centers"]:
        raise ValidationError(f"中心校 {center_id} 不是方案 {scheme_id} 的参与方")
    if not reason or not reason.strip():
        raise ValidationError("异议必须附带理由")
    state.objections[(scheme_id, center_id)] = {
        "scheme": scheme_id,
        "center": center_id,
        "day": day,
        "reason": reason.strip(),
    }
    return scope_view(catalog, state, scheme_id, day)


def binding_schemes_for(catalog, state, classroom_id, day):
    """某日对该班有约束力的方案视图。"""
    result = []
    for scheme in catalog.schemes():
        if classroom_id not in scheme["classes"]:
            continue
        view = scope_view(catalog, state, scheme["id"], day)
        if view["binding"] and classroom_id in view["included_classes"]:
            result.append(view)
    return result
