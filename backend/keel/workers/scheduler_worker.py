"""调度器进程（APScheduler）。

只注册 `keel.scheduler.jobs.JOBS` 里的批处理任务。
不注册交易任务、不注册回测执行——这两件事的触发者分别是策略运行时和
backtest-worker 自己，调度器插手就会产生第二个真相。
"""

from __future__ import annotations

from keel.logging import configure_logging, get_logger
from keel.scheduler import jobs

log = get_logger(__name__)

JOB_COUNT = len(jobs.JOBS)


def main() -> None:
    configure_logging("scheduler")
    log.info("scheduler started", extra={"jobs": JOB_COUNT})
    try:
        jobs.register(None)
    except NotImplementedError:
        log.warning("scheduler registration not implemented yet; exiting")


if __name__ == "__main__":
    main()
