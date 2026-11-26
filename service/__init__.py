"""联办教学点教材选用与订货承诺后端。

模块划分：

* :mod:`service.repository` —— 县教务接口资料的只读目录；
* :mod:`service.state` —— 运行期状态与不可重算的承诺行；
* :mod:`service.districts` —— 片区归属、共建与授权优先级；
* :mod:`service.schemes` —— 共同方案范围、确认、异议与约束力；
* :mod:`service.decisions` —— 班级终选、切换、裁决与后备转用；
* :mod:`service.procurement` —— 订货承诺、减单改货与后备出单；
* :mod:`service.exchange` —— 合作方交换契约客户端；
* :mod:`service.api` —— 教务与采购共用的 HTTP 接口。
"""

from .errors import (
    AuthorizationError,
    IdempotencyConflict,
    NotFound,
    ScopeConflict,
    ServiceError,
    Uncommittable,
    ValidationError,
)
from .exchange import ExchangeClient, FakeExchange
from .repository import Catalog
from .service import SelectionService

__all__ = [
    "AuthorizationError",
    "Catalog",
    "ExchangeClient",
    "FakeExchange",
    "IdempotencyConflict",
    "NotFound",
    "ScopeConflict",
    "ServiceError",
    "SelectionService",
    "Uncommittable",
    "ValidationError",
]
