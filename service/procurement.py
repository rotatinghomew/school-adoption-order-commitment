"""采购订货承诺与后备出单。

承诺口径独立于选用重算：

* 只有存在唯一终选（``committable_book``）的班才能签出承诺；
* 同一班同一科目只允许一个书种的承诺行，重复报订在落库前拦截；
* 合作方受理后断连时凭原操作号轮询，不重造单号；
* 减单/改货走交换契约的 ``adjust``，结果未知前原数量继续占用，
  已确认册数不会因清单重算而消失；
* 后备选用只对未被有效承诺覆盖的册数出单，避免同一班到两套书。
"""

from .decisions import require_committable
from .errors import Uncommittable, ValidationError
from .state import Commitment


def _check_quantity(quantity, *, allow_zero=False):
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise ValidationError("数量必须是整数")
    minimum = 0 if allow_zero else 1
    if quantity < minimum:
        raise ValidationError(f"数量必须大于等于 {minimum}")


class ProcurementService:
    """面向县采购窗口的承诺服务。"""

    def __init__(self, catalog, state, exchange):
        self.catalog = catalog
        self.state = state
        self.exchange = exchange

    # -- 内部工具 -------------------------------------------------------

    def _occupied(self, classroom_id):
        commitment = self.state.commitment_for(classroom_id)
        return commitment.quantity if commitment is not None else 0

    def _capacity(self, classroom_id):
        return self.catalog.classroom(classroom_id)["count"]

    def _call_exchange(self, body):
        interrupted, _ = self.exchange.submit(body)
        result = {"interrupted": interrupted}
        # 伙伴首次查询可能仍是 accepted，按契约继续查到 completed；
        # 传输失败/404 时返回 None，承诺保持受理占位，等待后续轮询。
        receipt = None
        for _ in range(5):
            receipt = self.exchange.poll(body["operation_id"])
            if receipt is None or receipt.get("state") == "completed":
                break
        result["receipt"] = receipt
        return result

    # -- 正式报订 -------------------------------------------------------

    def reserve(self, *, operation_id, supplier, classroom_id, edition, quantity, day):
        """按被确认的班级需求签出订货承诺。"""
        _check_quantity(quantity)
        view = require_committable(self.catalog, self.state, classroom_id, day)
        if edition != view["committable_book"]:
            raise Uncommittable(
                "报订书种与班级唯一终选不一致，未确认一方不能抢占该班选择",
                details={
                    "classroom": classroom_id,
                    "requested_edition": edition,
                    "committable_edition": view["committable_book"],
                    "pending_reason": view["pending_reason"],
                },
            )
        existing = self.state.commitment_for(classroom_id)
        if existing is not None and existing.book != edition:
            raise Uncommittable(
                "同一班同一科目只能按一个最终选择报订",
                details={"classroom": classroom_id, "existing_book": existing.book},
            )
        free = self._capacity(classroom_id) - self._occupied(classroom_id)
        if quantity > free:
            raise ValidationError(
                "报订数量超过班级未被有效承诺覆盖的册数",
                details={
                    "classroom": classroom_id,
                    "requested": quantity,
                    "free_capacity": free,
                },
            )
        # 全部业务校验通过后再做幂等登记，避免失败请求污染操作号
        record = {
            "operation_id": operation_id,
            "action": "reserve",
            "supplier": supplier,
            "classroom": classroom_id,
            "edition": edition,
            "quantity": quantity,
            "day": day,
        }
        already = self.state.register_operation(operation_id, record)
        if already:
            return self._commitment_view(classroom_id, repeated=True)

        exchange_body = {
            "operation_id": operation_id,
            "action": "reserve",
            "supplier": supplier,
            "classroom": classroom_id,
            "edition": edition,
            "quantity": quantity,
        }
        exchange_result = self._call_exchange(exchange_body)
        total = self._occupied(classroom_id) + quantity
        commitment = existing or Commitment(
            classroom=classroom_id, book=edition, quantity=0,
            operation_id=operation_id, supplier=supplier,
        )
        commitment.quantity = total
        commitment.history.append(
            {"operation_id": operation_id, "action": "reserve",
             "quantity": quantity, "day": day}
        )
        receipt = exchange_result["receipt"]
        if receipt is not None and receipt.get("state") == "completed":
            commitment.state = "completed"
            commitment.receipt_id = receipt.get("receipt_id")
        self.state.put_commitment(commitment)
        return self._commitment_view(classroom_id)

    # -- 减单/改货 ------------------------------------------------------

    def adjust(self, *, operation_id, supplier, classroom_id, edition, quantity,
               reserve_operation_id, day):
        """对已受理/已确认的承诺行按交换契约办理减单（数量为新的绝对册数）。"""
        _check_quantity(quantity, allow_zero=True)
        commitment = self.state.commitment_for(classroom_id)
        if commitment is None:
            raise ValidationError("该班没有可调整的承诺行")
        if reserve_operation_id != commitment.operation_id and not any(
            item["operation_id"] == reserve_operation_id
            for item in commitment.history
        ):
            raise ValidationError(
                "调整必须关联该班已发出的原受理操作号",
                details={"reserve_operation_id": reserve_operation_id},
            )
        if edition != commitment.book:
            raise ValidationError(
                "改货请先将原书种减至零，再按新终选重新报订；"
                "一个承诺行不能同时挂两个书种",
                details={"classroom": classroom_id, "book": commitment.book},
            )
        if quantity > commitment.quantity:
            raise ValidationError(
                "调整后的册数不能高于当前承诺册数；增加数量请重新报订",
                details={"current": commitment.quantity, "requested": quantity},
            )
        record = {
            "operation_id": operation_id,
            "action": "adjust",
            "supplier": supplier,
            "classroom": classroom_id,
            "edition": edition,
            "quantity": quantity,
            "reserve_operation_id": reserve_operation_id,
            "day": day,
        }
        already = self.state.register_operation(operation_id, record)
        if already:
            return self._commitment_view(classroom_id, repeated=True)

        exchange_body = {
            "operation_id": operation_id,
            "action": "adjust",
            "supplier": supplier,
            "classroom": classroom_id,
            "edition": edition,
            "quantity": quantity,
            "reserve_operation_id": reserve_operation_id,
        }
        previous_state = commitment.state
        commitment.state = "adjustment_pending"
        commitment.history.append(
            {"operation_id": operation_id, "action": "adjust",
             "quantity": quantity, "reserve_operation_id": reserve_operation_id,
             "day": day, "outcome": "pending"}
        )
        receipt = self._call_exchange(exchange_body)["receipt"]
        if receipt is not None and receipt.get("state") == "completed":
            commitment.quantity = quantity
            commitment.state = "completed" if quantity > 0 else "closed"
            commitment.receipt_id = receipt.get("receipt_id", commitment.receipt_id)
            commitment.history[-1]["outcome"] = "completed"
        else:
            # 结果未知：原承诺册数继续占用，等待后续轮询
            commitment.state = previous_state
            commitment.history[-1]["outcome"] = "unknown_keeps_original"
        return self._commitment_view(classroom_id)

    def poll_adjustment(self, operation_id):
        """轮询调整结果；确认完成后才改写承诺册数。"""
        record = self.state.operations.get(operation_id)
        if record is None:
            return None
        receipt = self.exchange.poll(operation_id)
        if receipt is None or receipt.get("state") != "completed":
            return {"operation_id": operation_id, "state": "pending"}
        classroom_id = record["classroom"]
        commitment = self.state.commitment_for(classroom_id)
        if record["action"] == "adjust":
            commitment.quantity = record["quantity"]
            commitment.state = "completed" if record["quantity"] > 0 else "closed"
            commitment.receipt_id = receipt.get("receipt_id", commitment.receipt_id)
            for item in reversed(commitment.history):
                if item["operation_id"] == operation_id:
                    item["outcome"] = "completed"
                    break
        else:
            commitment.state = "completed"
            commitment.receipt_id = receipt.get("receipt_id", commitment.receipt_id)
        return {"operation_id": operation_id, "state": "completed",
                "receipt_id": commitment.receipt_id}

    # -- 后备出单 -------------------------------------------------------

    def reserve_fallback(self, *, operation_id, supplier, classroom_id, quantity, day):
        """后备书目只对未被有效承诺覆盖的册数出单，独立成行不覆盖正式承诺。"""
        _check_quantity(quantity)
        classroom = self.catalog.classroom(classroom_id)
        fallback = self.catalog.fallback_choice(classroom["subject"])
        if fallback is None:
            raise Uncommittable("该科目没有后备书目")
        transfer = self.state.transfers.get(classroom_id)
        if transfer is None or transfer["book"] != fallback["id"] or transfer["day"] > day:
            raise Uncommittable(
                "后备出单必须先有县教务的后备转用决定，且只对未承诺部分出单",
                details={"classroom": classroom_id},
            )
        occupied = self._occupied(classroom_id) + self._fallback_occupied(classroom_id)
        free = self._capacity(classroom_id) - occupied
        if quantity > free:
            raise ValidationError(
                "后备选用只能覆盖未被有效承诺占用的册数",
                details={"classroom": classroom_id, "free_capacity": free},
            )
        record = {
            "operation_id": operation_id,
            "action": "reserve",
            "supplier": supplier,
            "classroom": classroom_id,
            "edition": fallback["id"],
            "quantity": quantity,
            "day": day,
            "fallback": True,
        }
        already = self.state.register_operation(operation_id, record)
        if already:
            return self._commitment_view(classroom_id, repeated=True)
        exchange_body = {k: v for k, v in record.items() if k not in ("day", "fallback")}
        exchange_result = self._call_exchange(exchange_body)
        commitment = self.state.fallback_commitments.get(classroom_id) or Commitment(
            classroom=classroom_id, book=fallback["id"], quantity=0,
            operation_id=operation_id, supplier=supplier,
        )
        commitment.quantity += quantity
        commitment.history.append(
            {"operation_id": operation_id, "action": "reserve_fallback",
             "quantity": quantity, "day": day}
        )
        receipt = exchange_result["receipt"]
        if receipt is not None and receipt.get("state") == "completed":
            commitment.state = "completed"
            commitment.receipt_id = receipt.get("receipt_id")
        self.state.fallback_commitments[classroom_id] = commitment
        return self._commitment_view(classroom_id)

    # -- 查询 -----------------------------------------------------------

    def _fallback_occupied(self, classroom_id):
        commitment = self.state.fallback_commitments.get(classroom_id)
        return commitment.quantity if commitment is not None else 0

    def _commitment_view(self, classroom_id, *, repeated=False):
        commitment = self.state.commitment_for(classroom_id)
        fallback = self.state.fallback_commitments.get(classroom_id)
        return {
            "classroom": classroom_id,
            "capacity": self._capacity(classroom_id),
            "repeated_operation": repeated,
            "commitment": commitment.to_dict() if commitment is not None else None,
            "fallback_commitment": fallback.to_dict() if fallback is not None else None,
            "effective_quantity": commitment.quantity if commitment is not None else 0,
            "fallback_quantity": fallback.quantity if fallback is not None else 0,
        }

    def class_order_status(self, classroom_id, day):
        """县采购按班查仍有效的数量及待调整原因。"""
        from .decisions import resolve

        view = resolve(self.catalog, self.state, classroom_id, day)
        commitment = self.state.commitment_for(classroom_id)
        pending = []
        if commitment is not None:
            for item in commitment.history:
                if item.get("outcome") == "unknown_keeps_original":
                    pending.append({
                        "operation_id": item["operation_id"],
                        "reason": "交换契约结果未知，原承诺册数继续占用",
                        "quantity_on_hold": commitment.quantity,
                    })
        fallback_commitment = self.state.fallback_commitments.get(classroom_id)
        return {
            "classroom": classroom_id,
            "day": day,
            "capacity": self._capacity(classroom_id),
            "effective_quantity": commitment.quantity if commitment is not None else 0,
            "book": commitment.book if commitment is not None else None,
            "commitment_state": commitment.state if commitment is not None else None,
            "fallback_book": (
                fallback_commitment.book if fallback_commitment is not None else None
            ),
            "fallback_quantity": (
                fallback_commitment.quantity if fallback_commitment is not None else 0
            ),
            "uncovered_quantity": self._capacity(classroom_id) - (
                (commitment.quantity if commitment is not None else 0)
                + (fallback_commitment.quantity if fallback_commitment is not None else 0)
            ),
            "pending_adjustments": pending,
            "selection": {
                "authorized_book": view["authorized_book"],
                "in_use_book": view["in_use_book"],
                "committable_book": view["committable_book"],
                "pending_reason": view["pending_reason"],
            },
        }
