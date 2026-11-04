"""CCXT OHLCV → 统一 K 线表。

CCXT 的 OHLCV 是 ``[ms, open, high, low, close, volume]`` 六元组，返回顺序不保证、
同一根 bar 可能因交易所修订出现两次。归一化把这些都吃掉，下游只看一种形状。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pandas as pd

from keel.data.schemas import BARS_COLUMNS

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


class BadBar(ValueError):
    """单根 bar 自身不自洽（high < low 之类）。这种数据修不了，只能拒。"""


def interval_to_ms(interval: str) -> int:
    try:
        return INTERVAL_MS[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported interval: {interval}") from exc


def normalize_ohlcv(
    rows: Sequence[Sequence[float]],
    symbol: str,
    interval: str,
    *,
    ingested_at: datetime | None = None,
) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            {
                c: pd.Series(dtype="float64" if c not in ("symbol", "interval") else "object")
                for c in BARS_COLUMNS
            }
        )
    frame = pd.DataFrame(list(rows), columns=["ms", "open", "high", "low", "close", "volume"])
    frame = frame.dropna(subset=["ms", "open", "high", "low", "close", "volume"])
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = frame[col].astype("float64")

    # 自相矛盾的 bar 必须抛错，不能静默丢掉：静默丢弃会让缺口分析把
    # 「交易所给了错数据」记成「我们没拉到」，两类问题处置方式完全不同。
    bad_high = frame[frame["high"] < frame["low"]]
    if not bad_high.empty:
        raise BadBar(f"high < low at {int(bad_high['ms'].iloc[0])}")
    bad_vol = frame[frame["volume"] < 0]
    if not bad_vol.empty:
        raise BadBar(f"negative volume at {int(bad_vol['ms'].iloc[0])}")

    frame["open_time"] = pd.to_datetime(frame["ms"].astype("int64"), unit="ms", utc=True)
    frame = frame.sort_values("open_time").drop_duplicates("open_time", keep="last")
    frame["symbol"] = symbol
    frame["interval"] = interval
    frame["revision"] = 1
    stamp = ingested_at or datetime.now(UTC)
    frame["ingested_at"] = pd.Timestamp(stamp)
    return frame[list(BARS_COLUMNS)].reset_index(drop=True)
