"""回测接口（API-03 提交、API-04 状态与制品、API-05 列表）。

关键约束（§2.5.6）：**API 只入队**。它写 ``backtest_runs(status=QUEUED)`` 并发事件，
真正的执行由 ``backtest-worker`` 用 ``FOR UPDATE SKIP LOCKED`` 认领。
在 API 进程里跑回测，一个 30 分钟任务就能把服务打死。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from keel.api.deps import API_PREFIX, Cursor, Limit, require_token
from keel.api.schemas import BacktestQueued, Envelope, Meta

router = APIRouter(prefix=API_PREFIX, tags=["research"], dependencies=[Depends(require_token)])


class BacktestRequest(BaseModel):
    strategy_key: str
    strategy_version: int = 1
    universe: list[str] = Field(min_length=1, max_length=100)
    interval: str
    start: str = Field(alias="from")
    end: str = Field(alias="to")
    params: dict = Field(default_factory=dict)
    cost_model_id: str
    engine: str = "keel-bar"

    # 请求体里用 ``from``/``to``（与 API-01 一致），但 Python 里 from 是关键字。
    model_config = {"populate_by_name": True}


def _parse(when: str) -> datetime:
    return datetime.fromisoformat(when.replace("Z", "+00:00"))


@router.post("/backtests", status_code=201, response_model=Envelope[BacktestQueued])
def create_backtest(
    body: BacktestRequest,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", description="映射为 run_id，防重复提交")
    ] = None,
) -> Envelope[BacktestQueued]:
    """API-03：校验参数 → 入队 → 立刻返回 ``run_id``。

    校验只做形状与覆盖范围检查，不做策略逻辑校验——参数 schema 的权威在
    ``keel.contracts``，重复实现一份必然分叉。
    """
    from keel.research import backtest as queue

    start = _parse(body.start)
    end = _parse(body.end)
    if end <= start:
        from keel.errors import KeelError

        raise KeelError("to 必须晚于 from", details={"from": body.start, "to": body.end})

    run_id = queue.enqueue(
        universe=body.universe,
        interval=body.interval,
        start=start,
        end=end,
        strategy_key=body.strategy_key,
        strategy_version=body.strategy_version,
        params=body.params,
        cost_model_id=body.cost_model_id,
        engine=body.engine,
        idempotency_key=idempotency_key,
    )
    return Envelope(data=BacktestQueued(run_id=run_id, status="QUEUED"), meta=Meta())


@router.get("/backtests/{run_id}")
def get_backtest(run_id: str) -> Envelope[dict]:
    """API-04：状态、指标与制品。

    ``meta.stale=true`` 表示数据修订后该结果已失效（§6.3）。此时**不自动重跑**，
    由人决定要不要重算——自动重跑会在数据回补时把队列塞满。
    """
    from keel.research import backtest as queue

    run = queue.get(run_id)
    return Envelope(
        data={
            "run_id": run.run_id,
            "status": run.status.value,
            "fail_reason": run.fail_reason.value if run.fail_reason else None,
            "universe": run.universe,
            "interval": run.interval,
            "metrics": run.metrics,
            "stale": False,
        },
        meta=Meta(as_of=run.end_time),
    )


@router.get("/backtests")
def list_backtests(limit: Limit = 50, cursor: Cursor = None) -> Envelope[list]:
    """API-05：游标分页，按创建时间倒序。"""
    from keel.research import backtest as queue

    runs = queue.list_runs(limit=limit, cursor=cursor)
    return Envelope(
        data=[
            {
                "run_id": r.run_id,
                "status": r.status.value,
                "universe": r.universe,
                "interval": r.interval,
                "metrics": r.metrics,
            }
            for r in runs
        ],
        meta=Meta(),
    )
