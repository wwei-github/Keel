"""ETL：单写者、原子发布、集市内容正确。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from keel.data import etl
from keel.data.datasets import ODS_BARS
from keel.data.lake import partition_dir, write_partition
from keel.db import mart
from keel.market.normalize import normalize_ohlcv

HOUR = 3_600_000
START_MS = 1_699_200_000_000  # 2023-11-05T00:00:00Z


def _seed(root: Path, symbol: str, n: int = 40) -> None:
    rows = []
    for i in range(n):
        rows.append([START_MS + i * HOUR, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 5.0])
    frame = normalize_ohlcv(rows, symbol, "1h")
    path = partition_dir(root, ODS_BARS, symbol, "1h", datetime(2023, 11, 1, tzinfo=UTC))
    write_partition(path, frame)


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    root = tmp_path / "raw"
    _seed(root, "BTC/USDT")
    _seed(root, "ETH/USDT")
    return root


def test_run_once_builds_marts(lake: Path, tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    results = etl.run_once(root=lake, mart=target)
    assert {r.dataset for r in results} >= {"dwd_bars", "dws_features", "ops_coverage"}
    with mart.connect(target) as con:
        assert con.execute("select count(*) from dwd_bars").fetchone()[0] == 80
        # 特征是滚动窗口，前 20 根 sma_20 为空是正常的
        assert (
            con.execute("select count(*) from dws_features where sma_20 is not null").fetchone()[0]
            > 0
        )
        assert con.execute("select count(*) from ops_coverage").fetchone()[0] == 2
        assert con.execute("select count(*) from ds_meta").fetchone()[0] >= 3


def test_run_once_is_idempotent(lake: Path, tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    etl.run_once(root=lake, mart=target)
    with mart.connect(target) as con:
        first = con.execute("select count(*) from dwd_bars").fetchone()[0]
    etl.run_once(root=lake, mart=target)
    with mart.connect(target) as con:
        assert con.execute("select count(*) from dwd_bars").fetchone()[0] == first


def test_run_once_fails_fast_when_lock_held(lake: Path, tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    with mart.writer_lock(target.parent), pytest.raises(mart.MartBusy):
        etl.run_once(root=lake, mart=target)


def test_run_once_subset(lake: Path, tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    results = etl.run_once(datasets=["dwd_bars"], root=lake, mart=target)
    assert [r.dataset for r in results] == ["dwd_bars"]
