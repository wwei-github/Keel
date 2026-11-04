"""Parquet 湖：分区命名、原子写、往返一致。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from keel.data.datasets import ODS_BARS
from keel.data.lake import (
    list_partitions,
    partition_dir,
    quote_symbol,
    read_bars,
    unquote_symbol,
    write_partition,
)


def _frame(symbol: str = "BTC/USDT") -> pd.DataFrame:
    ts = pd.to_datetime(["2023-11-01T00:00:00Z", "2023-11-01T01:00:00Z"], utc=True)
    return pd.DataFrame(
        {
            "open_time": ts,
            "symbol": [symbol, symbol],
            "interval": ["1h", "1h"],
            "open": [1.0, 2.0],
            "high": [3.0, 4.0],
            "low": [0.5, 1.5],
            "close": [2.0, 3.0],
            "volume": [10.0, 20.0],
            "revision": [1, 1],
            "ingested_at": pd.to_datetime(["2023-12-01T00:00:00Z"] * 2, utc=True),
        }
    )


def test_symbol_roundtrip_is_dir_safe() -> None:
    assert quote_symbol("BTC/USDT") == "BTC_USDT"
    assert unquote_symbol("BTC_USDT") == "BTC/USDT"


def test_partition_dir_follows_dataset_keys(tmp_path: Path) -> None:
    path = partition_dir(tmp_path, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 15, tzinfo=UTC))
    assert path == tmp_path / "ods_bars/symbol=BTC_USDT/interval=1h/year=2023/month=11"


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    path = partition_dir(tmp_path, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 1, tzinfo=UTC))
    written = write_partition(path, _frame())
    assert written == 2
    assert not list(path.glob("*.tmp")), "不允许留下临时文件"
    back = read_bars(tmp_path, symbol="BTC/USDT", interval="1h")
    assert len(back) == 2
    assert str(back["open_time"].dt.tz) == "UTC"
    assert back["close"].tolist() == [2.0, 3.0]


def test_read_filters_by_symbol(tmp_path: Path) -> None:
    for sym in ("BTC/USDT", "ETH/USDT"):
        path = partition_dir(tmp_path, ODS_BARS, sym, "1h", datetime(2023, 11, 1, tzinfo=UTC))
        write_partition(path, _frame(sym))
    assert len(read_bars(tmp_path, symbol="ETH/USDT")) == 2
    assert len(read_bars(tmp_path)) == 4


def test_list_partitions(tmp_path: Path) -> None:
    path = partition_dir(tmp_path, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 1, tzinfo=UTC))
    write_partition(path, _frame())
    assert list_partitions(tmp_path, ODS_BARS) == [("BTC/USDT", "1h", 2023, 11)]


def test_write_partition_rejects_schema_drift(tmp_path: Path) -> None:
    path = partition_dir(tmp_path, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 1, tzinfo=UTC))
    bad = _frame().drop(columns=["volume"])
    with pytest.raises(ValueError, match="missing columns"):
        write_partition(path, bad)


def test_read_keeps_one_row_per_bar(tmp_path: Path) -> None:
    """只追加的写规则会让同一根 bar 出现多份，读侧必须收敛成一行。

    ODS 不覆盖是刻意的（revision 历史有用），但下游要的是"每根 bar 一行"。
    把去重放在写侧相当于用丢历史换整齐，方向反了。
    """
    from keel.data.datasets import ODS_BARS
    from keel.data.lake import partition_dir, write_partition

    stamp = pd.Timestamp("2023-11-01T00:00Z")
    for revision, close in ((1, 1.0), (2, 1.5), (1, 1.2)):
        frame = pd.DataFrame(
            {
                "open_time": [stamp],
                "symbol": ["BTC/USDT"],
                "interval": ["1h"],
                "open": [1.0],
                "high": [2.0],
                "low": [0.5],
                "close": [close],
                "volume": [10.0],
                "revision": [revision],
                "ingested_at": pd.to_datetime(["2023-12-01T00:00Z"], utc=True),
            }
        )
        write_partition(partition_dir(tmp_path, ODS_BARS, "BTC/USDT", "1h", stamp), frame)

    frame = read_bars(tmp_path, symbol="BTC/USDT", interval="1h")
    assert len(frame) == 1
    assert frame["close"].iloc[0] == 1.5  # 最新 revision 胜出
