"""合作方交换契约客户端。

对应 ``partner/exchange.json``：

* ``POST /order-lines`` 首次受理回 202，同号同单可重复受理，
  同号不同单回 409，未知操作号查询回 404；
* 伙伴可能在受理后断连（``--disconnect-after-accept``），调用方
  必须能在断连后用原操作号轮询回执，而不是重新造号；
* 回执从 ``accepted`` 转为 ``completed`` 并给出 ``receipt_id``。

客户端只做协议交互与重试，承诺口径由 :mod:`service.procurement` 持有。
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import IdempotencyConflict, ServiceError, ValidationError


class ExchangeClient:
    """基于标准库 urllib 的合作方客户端。"""

    def __init__(self, base_url, route="/order-lines", timeout=3.0):
        self.base_url = base_url.rstrip("/")
        self.route = route
        self.timeout = timeout

    def _post(self, body):
        request = Request(
            self.base_url + self.route,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            payload = self._read_error(exc)
            if exc.code == 409:
                raise IdempotencyConflict(
                    payload.get("error", "相同操作号关联了不同受理单"),
                    details=payload,
                )
            if exc.code == 400:
                raise ValidationError(
                    payload.get("error", "受理单无法识别"), details=payload
                )
            raise ServiceError(
                f"合作方返回 {exc.code}", details=payload
            )
        except (URLError, ConnectionError, OSError) as exc:
            # 受理可能已在伙伴侧落库，交由调用方凭操作号查询确认
            return None, {"transport_error": str(exc)}

    def _get(self, operation_id):
        try:
            with urlopen(
                f"{self.base_url}{self.route}/{operation_id}", timeout=self.timeout
            ) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            if exc.code == 404:
                return 404, None
            payload = self._read_error(exc)
            raise ServiceError(
                f"合作方返回 {exc.code}", details=payload
            )
        except (URLError, ConnectionError, OSError) as exc:
            return None, {"transport_error": str(exc)}

    @staticmethod
    def _read_error(exc):
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeError):
            return {}

    def submit(self, body):
        """提交受理单；返回 (是否断连待查, 回执)。"""
        status, payload = self._post(body)
        if status is None:
            return True, payload
        return False, payload

    def poll(self, operation_id):
        """查询回执；404 与传输失败都返回 None，由调用方安排重试。"""
        status, payload = self._get(operation_id)
        if status == 404 or status is None:
            return None
        return payload


class FakeExchange:
    """测试用内存假伙伴，保留与真实服务一致的受理语义。"""

    def __init__(self, disconnect_once=False):
        self.receipts = {}
        self.reads = {}
        self.disconnect_once = disconnect_once

    def submit(self, body):
        key = body["operation_id"]
        previous = self.receipts.get(key)
        if previous is not None and previous != body:
            raise IdempotencyConflict("相同操作号关联了不同受理单")
        interrupted = False
        if previous is None:
            self.receipts[key] = dict(body)
            self.reads[key] = 0
            if self.disconnect_once:
                self.disconnect_once = False
                interrupted = True
        if interrupted:
            return True, {"transport_error": "simulated disconnect after accept"}
        return False, {"operation_id": key, "state": "accepted"}

    def poll(self, operation_id):
        body = self.receipts.get(operation_id)
        if body is None:
            return None
        # 与伙伴服务一致：首次查询仍是 accepted，第二次才 completed
        self.reads[operation_id] += 1
        if self.reads[operation_id] < 2:
            return {"operation_id": operation_id, "state": "accepted"}
        return {
            "operation_id": operation_id,
            "state": "completed",
            "receipt_id": "demo-" + operation_id,
            "request": body,
        }
