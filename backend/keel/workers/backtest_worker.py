"""回测执行器。

认领方式：`SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`。
默认并发度 1——回测是内存与 CPU 密集型，并行只会互相拖慢并触发 OOM。

启动时先清理孤儿：把 RUNNING 且 `started_at` 早于 2 倍 timeout 的任务
置为 FAILED(ORPHANED)。不清的话，被 kill -9 的任务会永远卡在 RUNNING，
队列看起来"满了"但其实没人干活。
"""

from __future__ import annotations

import time

from keel.logging import configure_logging, get_logger
from keel.ops import heartbeat
from keel.research import backtest

log = get_logger(__name__)


def main() -> None:
    configure_logging("backtest-worker")
    log.info("backtest worker started")
    while True:
        try:
            heartbeat.beat("backtest-worker")
            run = backtest.claim_next()
            if run is None:
                time.sleep(2)
                continue
            log.info("claimed run", extra={"run_id": run.run_id})
        except NotImplementedError:
            log.warning("backtest execution not implemented yet; idling")
            time.sleep(60)
        except Exception:
            log.exception("backtest failed")
            time.sleep(5)


if __name__ == "__main__":
    main()
