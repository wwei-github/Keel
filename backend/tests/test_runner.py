"""执行一条回测：读原始湖 → 逐 bar 模拟 → 返回 metrics。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from keel.data.datasets import ODS_BARS
from keel.data.lake import partition_dir, write_partition
from keel.market.normalize import normalize_ohlcv
from keel.research.backtest import BacktestRun, RunStatus
from keel.research.runner import execute_run

HOUR = 3_600_000


def _seed(root: Path, n: int = 200) -> None:
    import math

    rows = []
    for i in range(n):
        px = 100 + 10 * math.sin(i / 12)
        rows.append([1_699_200_000_000 + i * HOUR, px, px * 1.01, px * 0.99, px, 5.0])
    frame = normalize_ohlcv(rows, "BTC/USDT", "1h")
    write_partition(
        partition_dir(root, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 1, tzinfo=UTC)), frame
    )


def test_execute_run_produces_metrics(tmp_path: Path) -> None:
    _seed(tmp_path)
    run = BacktestRun(
        run_id="r1",
        status=RunStatus.RUNNING,
        universe=["BTC/USDT"],
        interval="1h",
        start_time=datetime(2023, 11, 1, tzinfo=UTC),
        end_time=datetime(2023, 11, 20, tzinfo=UTC),
        strategy_key="sma_cross",
        strategy_version=1,
        params={"fast": 5, "slow": 20},
        cost_model_id="default",
        engine="keel-bar",
    )
    out = execute_run(run, raw_root=tmp_path)
    assert set(out["metrics"]) >= {"sharpe", "max_drawdown", "trade_count"}
    assert out["bars"] > 100


def test_execute_run_rejects_unknown_strategy(tmp_path: Path) -> None:
    _seed(tmp_path, 10)
    run = BacktestRun(
        run_id="r2",
        status=RunStatus.RUNNING,
        universe=["BTC/USDT"],
        interval="1h",
        start_time=datetime(2023, 11, 1, tzinfo=UTC),
        end_time=datetime(2023, 11, 2, tzinfo=UTC),
        strategy_key="nope",
        strategy_version=1,
        params={},
        cost_model_id="default",
        engine="keel-bar",
    )
    with pytest.raises(KeyError):
        execute_run(run, raw_root=tmp_path)
