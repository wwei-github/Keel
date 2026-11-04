"""按主数据把该补的 K 线补齐。

调度器只负责「到点了」，补什么、补多久由这里决定：从 ``instruments`` 表
取状态为 TRADING 的标的，按 ``plan`` 裁掉已存在的区间，再逐段拉。

回补失败**不中断其他标的**：一个标的限流被拒不该让 49 个标的都补不上，
但失败的标的必须出现在返回值里并被上层告警——静默跳过等于制造缺口。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from keel.logging import get_logger

log = get_logger(__name__)


def enabled_symbols() -> list[str]:
    from keel.db.pool import connection

    with connection() as conn:
        rows = conn.execute(
            "select symbol from instruments where status = 'TRADING' order by symbol"
        ).fetchall()
    return [r["symbol"] for r in rows]


def run_for_enabled_instruments(
    *,
    interval: str = "1h",
    lookback_days: int = 2,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """返回 ``{symbol: 写入行数}``。异常按标的隔离。"""
    from keel.config import get_settings
    from keel.market.backfill import BackfillJob, execute, plan
    from keel.market.exchange import BinanceSpot

    symbols = enabled_symbols()
    if not symbols:
        # 空清单不报警：V1 冷启动时主数据还没同步，这是正常状态
        log.info("no enabled instruments; nothing to backfill")
        return {}

    stop = now or datetime.now(UTC)
    start = stop - timedelta(days=lookback_days)
    raw_root = Path(root or get_settings().raw_root)
    jobs = [BackfillJob(symbol, interval, start, stop) for symbol in symbols]

    written: dict[str, int] = {}
    exchange = BinanceSpot()
    for todo in plan(jobs, root=raw_root):
        try:
            result = execute(todo, exchange=exchange, root=raw_root)
        except Exception:
            log.exception("backfill failed", extra={"symbol": todo.symbol})
            written[todo.symbol] = -1  # 负数表示这个标的失败，交给上层告警
            continue
        written[todo.symbol] = written.get(todo.symbol, 0) + result.rows_written
    return written
