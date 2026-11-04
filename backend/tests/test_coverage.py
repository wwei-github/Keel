"""缺口分级：连续缺 2 根以上是 blocking，单根是 tolerated。§3.1"""

from __future__ import annotations

import pandas as pd

from keel.data.coverage import analyse


def _bars(starts: list[str], interval: str = "1h") -> pd.DataFrame:
    ts = pd.to_datetime(starts, utc=True)
    return pd.DataFrame(
        {
            "open_time": ts,
            "symbol": ["BTC/USDT"] * len(ts),
            "interval": [interval] * len(ts),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "revision": 1,
            "ingested_at": pd.Timestamp("2023-12-01T00:00:00Z"),
        }
    )


def test_continuous_range_has_no_gaps() -> None:
    cov = analyse(_bars(["2023-11-01T00:00Z", "2023-11-01T01:00Z", "2023-11-01T02:00Z"]), "1h")
    assert cov.present == 3 == cov.expected
    assert cov.gaps == ()


def test_single_missing_bar_is_tolerated() -> None:
    cov = analyse(_bars(["2023-11-01T00:00Z", "2023-11-01T02:00Z"]), "1h")
    assert cov.expected == 3 and cov.present == 2
    assert [(g.missing, g.severity) for g in cov.gaps] == [(1, "tolerated")]


def test_two_missing_bars_is_blocking() -> None:
    cov = analyse(_bars(["2023-11-01T00:00Z", "2023-11-01T03:00Z"]), "1h")
    assert [(g.missing, g.severity) for g in cov.gaps] == [(2, "blocking")]


def test_empty_frame_reports_blocking_gap() -> None:
    cov = analyse(_bars([]), "1h")
    assert cov.start is None and cov.end is None
    assert [(g.missing, g.severity) for g in cov.gaps] == [(0, "blocking")]


def test_gap_start_end_are_the_missing_span() -> None:
    cov = analyse(_bars(["2023-11-01T00:00Z", "2023-11-01T03:00Z"]), "1h")
    gap = cov.gaps[0]
    assert str(gap.start) == "2023-11-01 01:00:00+00:00"
    assert str(gap.end) == "2023-11-01 02:00:00+00:00"
