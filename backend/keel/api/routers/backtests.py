"""回测接口（API-03 提交、API-04 状态与制品、API-05 列表）。

关键约束（§2.5.6）：**API 只入队**。它写 ``backtest_runs(status=QUEUED)`` 并发事件，
真正的执行由 ``backtest-worker`` 用 ``FOR UPDATE SKIP LOCKED`` 认领。
在 API 进程里跑回测，一个 30 分钟任务就能把服务打死。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from keel.api.deps import API_PREFIX, Cursor, Limit, require_token
from keel.api.schemas import Envelope
from keel.errors import NotImplementedYet

router = APIRouter(prefix=API_PREFIX, tags=["research"], dependencies=[Depends(require_token)])


class BacktestRequest(BaseModel):
    strategy_id: str
    symbol: str
    interval: str
    start: str
    end: str
    params: dict = Field(default_factory=dict)
    cost_model_id: str


@router.post("/backtests")
def create_backtest(
    body: BacktestRequest,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", description="映射为 run_id，防重复提交")
    ] = None,
) -> Envelope[dict]:
    """API-03：校验参数 → 入队 → 立刻返回 ``run_id``。

    校验只做形状与覆盖范围检查，不做策略逻辑校验——参数 schema 的权威在
    ``keel.contracts``，重复实现一份必然分叉。
    """
    raise NotImplementedYet("API-03 create_backtest 尚未实现（等待 backtest-worker）")


@router.get("/backtests/{run_id}")
def get_backtest(run_id: str) -> Envelope[dict]:
    """API-04：状态、指标与制品。

    ``meta.stale=true`` 表示数据修订后该结果已失效（§6.3）。此时**不自动重跑**，
    由人决定要不要重算——自动重跑会在数据回补时把队列塞满。
    """
    raise NotImplementedYet("API-04 get_backtest 尚未实现")


@router.get("/backtests")
def list_backtests(limit: Limit = 50, cursor: Cursor = None) -> Envelope[list]:
    """API-05：游标分页，按创建时间倒序。"""
    raise NotImplementedYet("API-05 list_backtests 尚未实现")
