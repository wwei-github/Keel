"""统一错误类型与原因码。

§6.1 规定失败响应形如 ``{ "error": { "code", "message", "details" }, "request_id" }``。
``code`` 必须是稳定枚举——前端靠它分支，改字符串等于破坏接口。
自由文本只放 ``message``，永远不要让人去解析 message 判断错误类型。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """接口错误码。新增可以，改已有值等于破坏契约。"""

    # 通用
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INTERNAL = "INTERNAL"

    # 数据（§3.3、§3.11）
    DATA_NOT_COVERED = "DATA_NOT_COVERED"
    DATA_STALE = "DATA_STALE"
    BLOCKING_GAP = "BLOCKING_GAP"

    # 回测（§2.5.6）
    BACKTEST_QUEUE_FULL = "BACKTEST_QUEUE_FULL"
    BACKTEST_TIMEOUT = "BACKTEST_TIMEOUT"

    # 交易（§3.5、§3.6）
    RISK_REJECTED = "RISK_REJECTED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    EXCHANGE_UNAVAILABLE = "EXCHANGE_UNAVAILABLE"
    ORDER_UNKNOWN_STATE = "ORDER_UNKNOWN_STATE"


class KeelError(Exception):
    """所有业务异常的基类。``code`` 进响应，``details`` 进排查。"""

    code: ErrorCode = ErrorCode.INTERNAL
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(KeelError):
    code = ErrorCode.NOT_FOUND
    http_status = 404


class NotCovered(KeelError):
    """请求的区间不在数据覆盖范围内。"""

    code = ErrorCode.DATA_NOT_COVERED
    http_status = 422


class NotImplementedYet(KeelError):
    """骨架阶段的占位：接口已定义、实现未落地。

    用它而不是 ``fastapi.HTTPException``，是为了让「未实现」也走统一错误形状，
    前端在骨架阶段就能按最终协议联调。
    """

    code = ErrorCode.NOT_IMPLEMENTED
    http_status = 501
