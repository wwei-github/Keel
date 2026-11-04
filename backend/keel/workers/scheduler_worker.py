"""调度器进程（APScheduler）。

只注册 `keel.scheduler.jobs.JOBS` 里的批处理任务。
不注册交易任务、不注册回测执行——这两件事的触发者分别是策略运行时和
backtest-worker 自己，调度器插手就会产生第二个真相。
"""

from __future__ import annotations

import time

from keel.logging import configure_logging, get_logger
from keel.scheduler import jobs

log = get_logger(__name__)

JOB_COUNT = len(jobs.JOBS)


def dispatch(job_id: str) -> None:
    """按 job_id 分派。未知 job_id 记 warning 而不是抛异常——
    抛异常会让 APScheduler 静默重试同一批任务，而配置错误重试不会变对。
    """
    from keel.data import etl
    from keel.ops import alerts, heartbeat
    from keel.scheduler.jobs import JOBS

    known = {j.job_id for j in JOBS}
    if job_id not in known:
        log.warning("unknown job", extra={"job_id": job_id})
        return

    started = time.monotonic()
    try:
        if job_id in ("sync-closed-bars", "daily-backfill"):
            from keel.tools import backfill

            backfill.run_for_enabled_instruments()
        elif job_id in ("build-marts", "coverage-check"):
            etl.run_once()
        elif job_id == "heartbeat-check":
            alerts.dead_man_switch()
            heartbeat.beat("scheduler")
        elif job_id == "feature-consistency-check":
            log.info("feature consistency check skipped (V2)")
        elif job_id == "weekly-research-report":
            log.info("weekly report skipped (V1 无实盘数据)")
        elif job_id in ("archive-and-compress", "export-tx"):
            log.info("maintenance job no-op in V1", extra={"job_id": job_id})
    except Exception:  # noqa: BLE001 — 批处理任务失败不能把调度器带崩
        log.exception("job failed", extra={"job_id": job_id})
        return
    log.info(
        "job done",
        extra={"job_id": job_id, "elapsed_ms": int((time.monotonic() - started) * 1000)},
    )


def main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    configure_logging("scheduler")
    log.info("scheduler started", extra={"jobs": JOB_COUNT})
    scheduler = BlockingScheduler(timezone="UTC")
    jobs.register(scheduler)
    scheduler.start()


if __name__ == "__main__":
    main()
