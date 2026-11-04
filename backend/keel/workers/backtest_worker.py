"""回测执行器。

认领方式：`SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`。
默认并发度 1——回测是内存与 CPU 密集型，并行只会互相拖慢并触发 OOM。

启动时先清理孤儿：把 RUNNING 且 `started_at` 早于 2 倍 timeout 的任务
置为 FAILED(ORPHANED)。不清的话，被 kill -9 的任务会永远卡在 RUNNING，
队列看起来"满了"但其实没人干活。
"""

from __future__ import annotations

import time
from pathlib import Path

from keel.config import get_settings
from keel.logging import configure_logging, get_logger
from keel.ops import heartbeat
from keel.research import backtest, runner

log = get_logger(__name__)


def main() -> None:
    configure_logging("backtest-worker")
    settings = get_settings()
    raw_root = Path(settings.raw_root)
    log.info("backtest worker started", extra={"raw_root": str(raw_root)})
    backtest.sweep_orphans(timeout_seconds=settings.backtest_timeout_seconds)
    while True:
        try:
            heartbeat.beat("backtest-worker")
            run = backtest.claim_next(timeout_seconds=settings.backtest_timeout_seconds)
            if run is None:
                time.sleep(2)
                continue
            log.info("claimed run", extra={"run_id": run.run_id})
            result = runner.execute_run(run, raw_root=raw_root)
            runner.persist_report(run, result)
            backtest.finish(run.run_id, metrics=result["metrics"])
            log.info("run finished", extra={"run_id": run.run_id})
        except Exception:
            log.exception("backtest failed")
            time.sleep(5)


if __name__ == "__main__":
    main()
