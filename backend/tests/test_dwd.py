"""明细层清洗：去重取最新 revision、标记异常 bar、不补造数据。"""

from __future__ import annotations

import pandas as pd

from keel.data.dwd import to_dwd


def _frame() -> pd.DataFrame:
    ts = pd.to_datetime(["2023-11-01T00:00Z", "2023-11-01T01:00Z", "2023-11-01T02:00Z"], utc=True)
    return pd.DataFrame(
        {
            "open_time": list(ts) + [ts[0]],
            "symbol": ["BTC/USDT"] * 4,
            "interval": ["1h"] * 4,
            "open": [1.0, 2.0, 3.0, 1.0],
            "high": [2.0, 3.0, 4.0, 2.0],
            "low": [0.5, 1.5, 2.5, 0.5],
            "close": [1.5, 2.5, 3.5, 1.6],
            "volume": [10.0, 11.0, 12.0, 10.5],
            "revision": [1, 1, 1, 2],
            "ingested_at": pd.to_datetime(["2023-12-01T00:00Z"] * 4, utc=True),
        }
    )


def test_keeps_latest_revision() -> None:
    out = to_dwd(_frame())
    assert len(out) == 3
    first = out[out["open_time"] == pd.Timestamp("2023-11-01T00:00Z")]
    assert first["close"].iloc[0] == 1.6
    assert first["revision"].iloc[0] == 2


def test_output_is_sorted() -> None:
    out = to_dwd(_frame())
    assert out["open_time"].is_monotonic_increasing


def test_does_not_invent_missing_bars() -> None:
    frame = _frame()
    frame = frame[frame["open_time"] != pd.Timestamp("2023-11-01T01:00Z")]
    out = to_dwd(frame)
    assert len(out) == 2  # 缺的那根就是缺着，不补 0 成交量假 bar


def test_outlier_flag_on_absurd_jump() -> None:
    frame = _frame()
    frame.loc[2, "high"] = 1000.0
    frame.loc[2, "low"] = 900.0
    frame.loc[2, "close"] = 950.0
    out = to_dwd(frame)
    assert out["is_outlier"].sum() == 1
    assert out.loc[out["is_outlier"], "open_time"].iloc[0] == pd.Timestamp("2023-11-01T02:00Z")


def _two_symbols(prices_btc: list[float], prices_eth: list[float]) -> pd.DataFrame:
    ts = pd.date_range("2023-11-01", periods=len(prices_btc), freq="h", tz="UTC")
    frames = []
    for symbol, prices in (("BTC/USDT", prices_btc), ("ETH/USDT", prices_eth)):
        frames.append(
            pd.DataFrame(
                {
                    "open_time": ts,
                    "symbol": symbol,
                    "interval": "1h",
                    "open": prices,
                    "high": [p * 1.01 for p in prices],
                    "low": [p * 0.99 for p in prices],
                    "close": prices,
                    "volume": 10.0,
                    "revision": 0,
                    "ingested_at": pd.Timestamp("2023-12-01", tz="UTC"),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_returns_are_not_computed_across_symbols() -> None:
    """两个价格量级差 20 倍的标的放在一起，不能算出 "收益"。

    不分组时，ETH 的首根减 BTC 的末根会得到一个 20 倍的价格比，MAD 基线被它抬高，
    两个标的的真实异常都被吃掉——表现是 is_outlier 全 False。
    """
    frame = _two_symbols([40000.0] * 30, [2000.0] * 30)
    out = to_dwd(frame)
    assert out["is_outlier"].sum() == 0


def test_outlier_on_each_symbol_is_judged_independently() -> None:
    btc = [40000.0] * 20
    # 单次跳空且不回头：只有一根 bar 的收益超过 HARD_JUMP，判定目标唯一。
    btc[15:] = [200000.0] * 5
    eth = [2000.0] * 20
    out = to_dwd(_two_symbols(btc, eth))
    flagged = out[out["is_outlier"]]
    assert len(flagged) == 1
    assert flagged["symbol"].iloc[0] == "BTC/USDT"
    assert flagged["open_time"].iloc[0] == pd.Timestamp("2023-11-01T15:00Z")


def test_output_is_time_ordered_within_symbol() -> None:
    out = to_dwd(_two_symbols([40000.0] * 10, [2000.0] * 10))
    for _, chunk in out.groupby("symbol"):
        assert chunk["open_time"].is_monotonic_increasing
