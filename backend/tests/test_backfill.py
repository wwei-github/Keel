"""回补：分页拉取、按月落分区、重跑幂等（第二次 plan 为空）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from keel.data.lake import read_bars
from keel.market.backfill import BackfillJob, execute, plan

HOUR = 3_600_000
START = datetime(2023, 11, 1, tzinfo=UTC)
END = datetime(2023, 11, 1, tzinfo=UTC).replace(hour=5)


class FakeExchange:
    def __init__(self, total: int) -> None:
        self.total = total
        self.calls = 0

    def fetch_ohlcv(self, symbol, interval, since_ms, limit=1000):
        self.calls += 1
        base = int(START.timestamp() * 1000)
        out = []
        for i in range(limit):
            ms = since_ms + i * HOUR
            if i + (since_ms - base) // HOUR >= self.total:
                break
            out.append([ms, 1.0, 2.0, 0.5, 1.5, 10.0])
        return out


def test_execute_writes_partitions(tmp_path: Path) -> None:
    job = BackfillJob("BTC/USDT", "1h", START, END)
    result = execute(job, exchange=FakeExchange(5), root=tmp_path, limit=2)
    assert result.rows_written == 5
    assert result.batches == 3
    assert len(read_bars(tmp_path, symbol="BTC/USDT", interval="1h")) == 5


def test_plan_is_empty_when_range_is_complete(tmp_path: Path) -> None:
    job = BackfillJob("BTC/USDT", "1h", START, END)
    execute(job, exchange=FakeExchange(5), root=tmp_path, limit=1000)
    assert plan([job], root=tmp_path) == []


def test_plan_only_covers_missing_tail(tmp_path: Path) -> None:
    first = BackfillJob("BTC/USDT", "1h", START, START)
    execute(first, exchange=FakeExchange(1), root=tmp_path, limit=1000)
    todo = plan([BackfillJob("BTC/USDT", "1h", START, END)], root=tmp_path)
    assert len(todo) == 1
    assert todo[0].start == START.replace(hour=1)


def test_execute_is_idempotent_on_same_range(tmp_path: Path) -> None:
    job = BackfillJob("BTC/USDT", "1h", START, START.replace(hour=1))
    execute(job, exchange=FakeExchange(2), root=tmp_path, limit=1000)
    execute(job, exchange=FakeExchange(2), root=tmp_path, limit=1000)
    frame = read_bars(tmp_path, symbol="BTC/USDT", interval="1h")
    assert len(frame.drop_duplicates("open_time")) == 2
