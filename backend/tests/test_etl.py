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


def test_features_are_per_symbol_not_mixed(lake: Path, tmp_path: Path) -> None:
    """特征必须按标的各算一份。

    这条测试是补出来的：原先整表一次性 ``compute``，``open_time`` 当索引，
    两个标的同一时刻各一行 → 索引重复，且滚动窗口跨标的续算
    （BTC 的尾部窗口参与 ETH 的 SMA）。数据上表现为行数对不上、
    特征值互相污染，而单标的回测完全看不出来。
    """
    target = tmp_path / "keel.duckdb"
    etl.run_once(root=lake, mart=target)
    with mart.connect(target) as con:
        rows = con.execute(
            "select symbol, count(*) as n, count(distinct open_time) as t"
            " from dws_features group by symbol order by symbol"
        ).fetchall()
        # 每个标的 40 根，没有因并表而增删
        assert [(r[0], r[1], r[2]) for r in rows] == [("BTC/USDT", 40, 40), ("ETH/USDT", 40, 40)]
        # 同一下标每个 (symbol, open_time) 只有一行 —— 没有撞车
        dupes = con.execute(
            "select count(*) from (select symbol, open_time from dws_features"
            " group by 1, 2 having count(*) > 1)"
        ).fetchone()[0]
        assert dupes == 0
        # 两个标的的 K 线完全相同（_seed 用同一套价格），所以特征也必须相同：
        # 只要出现过跨标的串算，前 20 根的窗口边界就会不同，值就会不等。
        diff = con.execute(
            """
            select count(*) from dws_features b
            join dws_features e
              on b.open_time = e.open_time and b.symbol='BTC/USDT' and e.symbol='ETH/USDT'
            where b.sma_20 is distinct from e.sma_20
               or b.atr_14 is distinct from e.atr_14
            """
        ).fetchone()[0]
        assert diff == 0


def test_features_carry_symbol_and_feature_version(lake: Path, tmp_path: Path) -> None:
    """没有 symbol 的特征表在集市里不可用：查某个标的的特征无从下手。"""
    target = tmp_path / "keel.duckdb"
    etl.run_once(root=lake, mart=target)
    with mart.connect(target) as con:
        cols = {r[0] for r in con.execute("describe dws_features").fetchall()}
        assert {"open_time", "symbol", "interval", "feature_version"} <= cols
        versions = con.execute("select distinct feature_version from dws_features").fetchall()
        assert [v[0] for v in versions] == ["feat-v1"]
