"""回测作业的认领与状态机。§2.5.6。

状态机：QUEUED → RUNNING → SUCCEEDED / FAILED(REASON)。

三个容易做错的点：
- 认领用 ``SELECT ... FOR UPDATE SKIP LOCKED``，否则多个 worker 会抢同一条。
- 超时置 ``FAILED(TIMEOUT)`` 且**不自动重试**：超时通常意味着参数或数据有问题，
  自动重试只是把同一个失败重演一遍。
- worker 启动时把 ``RUNNING`` 超过 2 倍 timeout 的任务置 ``FAILED(ORPHANED)``，
  否则进程被 kill 的任务会永远卡在 RUNNING。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class FailReason(StrEnum):
    TIMEOUT = "TIMEOUT"
    ORPHANED = "ORPHANED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class BacktestRun:
    run_id: str
    strategy_id: str
    status: RunStatus
    fail_reason: FailReason | None = None


def claim_next() -> BacktestRun | None:
    """认领一条 QUEUED 任务。默认并发度为 1（§2.5.6）。"""
    raise NotImplementedError("回测执行尚未实现：等待 Freqtrade 集成")
