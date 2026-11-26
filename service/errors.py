"""选用后端的领域错误。

所有错误都带稳定的 ``code``，HTTP 层据此映射状态码，便于教务与采购
两侧在同一份后端上按相同口径处理失败。
"""


class ServiceError(Exception):
    """领域错误基类。"""

    code = "service_error"
    status = 400

    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self):
        return {"error": self.code, "message": self.message, "details": self.details}


class ValidationError(ServiceError):
    """输入事实不成立或引用无法解析。"""

    code = "invalid_input"
    status = 400


class NotFound(ServiceError):
    """查询对象不存在。"""

    code = "not_found"
    status = 404


class AuthorizationError(ServiceError):
    """操作者对该班该课没有生效授权。"""

    code = "forbidden"
    status = 403


class ScopeConflict(ServiceError):
    """共同方案范围已变化，旧确认/旧异议不能沿用。"""

    code = "scope_conflict"
    status = 409


class Uncommittable(ServiceError):
    """当前不存在可报订的唯一终选（冲突未裁、书目暂停、无安排等）。"""

    code = "uncommittable"
    status = 409


class IdempotencyConflict(ServiceError):
    """同一操作号关联了不同内容。"""

    code = "idempotency_conflict"
    status = 409
