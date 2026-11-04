"""指标：年化因子显式、最大回撤符号正确。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from keel.research.metrics import compute_metrics


def _equity(values: list[float]) -> pd.Series:
    idx = pd.date_range("2023-01-01", periods=len(values), freq="1D", tz="UTC")
    return pd.Series(values, index=idx)


def test_total_return() -> None:
    m = compute_metrics(_equity([100.0, 110.0]), (), "1d")
    assert m["total_return"] == pytest.approx(0.10)


def test_max_drawdown_is_positive_magnitude() -> None:
    m = compute_metrics(_equity([100.0, 120.0, 60.0, 90.0]), (), "1d")
    assert m["max_drawdown"] == pytest.approx(0.5)


def test_cagr_uses_explicit_periods() -> None:
    # 365 天翻倍 → 年化 100%；用 252 会算成 ~1.9
    values = list(np.linspace(100.0, 200.0, 366))
    m = compute_metrics(_equity(values), (), "1d")
    assert m["cagr"] == pytest.approx(1.0, rel=0.02)


def test_sharpe_is_zero_for_flat_equity() -> None:
    m = compute_metrics(_equity([100.0] * 10), (), "1d")
    assert m["sharpe"] == 0.0


def test_trade_count_and_win_rate() -> None:
    class T:
        def __init__(self, pnl: float) -> None:
            self.pnl = pnl

    m = compute_metrics(_equity([100.0, 101.0]), (T(5.0), T(-2.0), T(1.0)), "1d")
    assert m["trade_count"] == 3
    assert m["win_rate"] == pytest.approx(2 / 3)
