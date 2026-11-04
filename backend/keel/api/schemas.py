"""HTTP 合约用的 Pydantic 模型。

与领域对象分开：``keel.contracts`` 里的 dataclass 是策略契约，这里的模型是线上协议。
两者形状相似时也不复用——契约改动不该默默改变 API 响应。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Meta(BaseModel):
    """§6.1：``meta`` 携带 ``request_id``、``as_of`` 与新鲜度标记。"""

    request_id: str | None = None
    as_of: datetime | None = Field(default=None, description="数据截止时间（UTC），用于判断新鲜度")
    stale: bool = Field(
        default=False,
        description="数据修订导致快照失效时为 true；前端必须显示提示，不得隐藏（§6.3）",
    )


class Envelope(BaseModel, Generic[T]):
    """成功响应统一 ``{ data, meta }``。"""

    data: T
    meta: Meta = Field(default_factory=Meta)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    request_id: str | None = None


class Bar(BaseModel):
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class HealthData(BaseModel):
    status: str
    version: str
    components: dict[str, str] = Field(default_factory=dict)


class KillSwitchState(BaseModel):
    active: bool = Field(description="true 表示停止新开仓；平仓与撤单不受影响")
    version: int = Field(description="控制面版本号，用于判断客户端缓存是否过期")
    updated_at: datetime | None = None
    updated_by: str | None = None
    reason: str | None = None


class BacktestQueued(BaseModel):
    run_id: str
    status: str = Field(description="入队后恒为 QUEUED，执行由 backtest-worker 认领")
