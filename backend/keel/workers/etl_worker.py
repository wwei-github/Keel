"""ETL 常驻进程。

单写者：全局只跑一个实例（docker-compose 里不允许 scale）。
拿不到 DuckDB 锁时直接失败并告警，不排队重试——排队只会把冲突推迟到更难查的时刻。
"""

from __future__ import annotations

import time

from keel.config import get_settings
from keel.data import etl
from keel.logging import configure_logging, get_logger
from keel.ops import heartbeat

log = get_logger(__name__)


def main() -> None:
    configure_logging("etl-worker")
    settings = get_settings()
    log.info("etl worker started", extra={"duckdb": settings.duckdb_path})
    while True:
        try:
            heartbeat.beat("etl-worker")
            done = etl.run_once()
            log.info("etl round finished", extra={"datasets": len(done)})
        except NotImplementedError:
            log.warning("etl not implemented yet; idling")
        except Exception:
            log.exception("etl round failed")
        time.sleep(300)


if __name__ == "__main__":
    main()
