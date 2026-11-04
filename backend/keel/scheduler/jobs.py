"""定时任务清单。§3.10。

六个任务，全部 UTC。时间点选在低活跃时段的理由写在每行注释里——
"为什么是 01:15 而不是 00:00"这种事，三个月后只有注释能回答。

本调度器**不触发交易、不触发回测执行**：前者该由策略运行时决定，
后者由 worker 自己认领。调度器只做批处理与维护。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    cron: str
    description: str


JOBS: tuple[Job, ...] = (
    Job("daily-backfill", "0 0 * * *", "回补日线缺口与交易对状态变更（UTC 00:00）"),
    Job("etl-hourly", "5 * * * *", "小时级 ETL；错开整点，避开行情写入高峰"),
    Job("feature-consistency-check", "15 1 * * *", "回测与实盘特征一致性比对（UTC 01:15）"),
    Job("export-tx", "20 * * * *", "PG→原始湖单向导出，按 updated_at 水位线"),
    Job("archive-and-compress", "30 2 * * *", "归档冷数据并压缩；删除动作在此显式发生"),
    Job("heartbeat-check", "*/5 * * * *", "检查心跳，缺失 3 个周期触发 dead-man switch"),
)


def register(scheduler) -> None:
    raise NotImplementedError("调度注册尚未实现：等待 APScheduler 装配")
