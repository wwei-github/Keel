"""定时任务清单。§3.10、§11.1。

九个任务，全部 UTC。时间点选在低活跃时段的理由写在每行注释里——
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
    # 必须排在 daily-backfill 之前：回补要读 instruments 表里哪些标的在交易。
    # 顺序颠倒不报错，只是那天一根 K 线都没补，日志只说「没有启用标的」。
    Job("sync-instruments", "0 0 * * *", "同步交易对主数据与上下架状态（UTC 00:00）"),
    Job("sync-closed-bars", "5 * * * *", "增量同步已收盘 K 线；错开整点，避开行情写入高峰"),
    Job("daily-backfill", "30 0 * * *", "回补日线缺口与交易对状态变更（UTC 00:30）"),
    # build-marts 与 coverage-check 分开跑：ETL 失败时覆盖率仍要说得出话，
    # 而覆盖率算的是刚建好的集市，所以必须排在它之后。
    Job("build-marts", "45 0 * * *", "重建 DuckDB 集市并原子发布（UTC 00:45）"),
    Job("coverage-check", "0 1 * * *", "计算覆盖率与缺口分级，写 ops_coverage（UTC 01:00）"),
    Job("feature-consistency-check", "15 1 * * *", "回测与实盘特征一致性比对（V2 起生效）"),
    Job("weekly-research-report", "30 1 * * 1", "周报：上周回测与数据质量摘要（周一 UTC 01:30）"),
    Job("archive-and-compress", "0 2 * * *", "归档冷数据并压缩；删除动作在此显式发生"),
    Job("export-tx", "20 * * * *", "PG→原始湖单向导出，按 updated_at 水位线"),
    Job("heartbeat-check", "*/5 * * * *", "检查心跳，缺失 3 个周期触发 dead-man switch"),
)


def register(scheduler) -> None:
    """把 JOBS 注册到 APScheduler。时区固定 UTC，不读调度器默认值。

    显式传 timezone 是必须的：APScheduler 默认用系统本地时区，
    在 macOS 上是 Asia/Shanghai，所有任务会整体偏 8 小时，
    而这种错误在本地测试里完全看不出来（都是相对时间）。
    """
    from zoneinfo import ZoneInfo

    from keel.workers import scheduler_worker

    for job in JOBS:
        scheduler.add_job(
            scheduler_worker.dispatch,
            trigger="cron",
            kwargs={"job_id": job.job_id},
            id=job.job_id,
            replace_existing=True,
            timezone=ZoneInfo("UTC"),
            misfire_grace_time=300,
        )
