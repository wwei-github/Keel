"""历史回补（CCXT REST）。§3.2。

幂等靠"写临时文件 → 校验 → 原子替换"，不靠唯一约束：原始湖是 Parquet，没有约束可用。

限流是硬约束：Binance 的 REST 权重限制写在这层，不写在调用点。
散落在各处的 ``sleep`` 最后一定会被删掉一个，然后在某个周末被封 IP。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BackfillJob:
    symbol: str
    interval: str
    start: datetime
    end: datetime


def plan(jobs: list[BackfillJob]) -> list[BackfillJob]:
    """把请求拆成可重跑的批次，按已有覆盖区间取差集。"""
    raise NotImplementedError("回补计划尚未实现：等待 ops_coverage 落地")


def execute(job: BackfillJob) -> None:
    """执行一个批次。失败整体重试该批次，不做部分成功——半批次的数据最难判断。"""
    raise NotImplementedError("回补执行尚未实现：等待 CCXT 适配器")
