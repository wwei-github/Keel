"""CCXT OHLCV 行的归一化：排序、去重、UTC、基本校验。"""

from __future__ import annotations

import pytest

from keel.data.schemas import BARS_COLUMNS
from keel.market.normalize import BadBar, interval_to_ms, normalize_ohlcv

MS = 1_700_000_000_000  # 2023-11-14T22:13:20Z


def test_columns_and_utc() -> None:
    df = normalize_ohlcv([[MS, 1.0, 2.0, 0.5, 1.5, 10.0]], "BTC/USDT", "1h")
    assert tuple(df.columns) == BARS_COLUMNS
    assert str(df["open_time"].dt.tz) == "UTC"
    assert df["symbol"].iloc[0] == "BTC/USDT"


def test_unsorted_input_is_sorted_and_deduped() -> None:
    rows = [
        [MS + 3_600_000, 2.0, 3.0, 1.5, 2.5, 20.0],
        [MS, 1.0, 2.0, 0.5, 1.5, 10.0],
        [MS, 1.0, 2.0, 0.5, 1.9, 11.0],  # 同一根 bar 的修订，保留后到的
    ]
    df = normalize_ohlcv(rows, "BTC/USDT", "1h")
    assert len(df) == 2
    assert df["close"].tolist() == [1.9, 2.5]


def test_high_below_low_is_rejected() -> None:
    with pytest.raises(BadBar, match="high < low"):
        normalize_ohlcv([[MS, 1.0, 0.4, 0.5, 1.0, 1.0]], "BTC/USDT", "1h")


def test_negative_volume_is_rejected() -> None:
    with pytest.raises(BadBar, match="negative volume"):
        normalize_ohlcv([[MS, 1.0, 2.0, 0.5, 1.5, -1.0]], "BTC/USDT", "1h")


def test_empty_input_returns_typed_empty_frame() -> None:
    df = normalize_ohlcv([], "BTC/USDT", "1h")
    assert len(df) == 0
    assert tuple(df.columns) == BARS_COLUMNS


def test_interval_to_ms() -> None:
    assert interval_to_ms("1h") == 3_600_000
    assert interval_to_ms("1d") == 86_400_000
    with pytest.raises(ValueError, match="unsupported interval"):
        interval_to_ms("7m")
