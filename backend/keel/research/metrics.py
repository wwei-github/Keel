"""回测指标。自己算，不调 QuantStats——指标是验收标准（A3/A4），
必须能逐行解释；QuantStats 只用于出报告。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from keel.reports.quantstats import periods_per_year


def _returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def compute_metrics(equity: pd.Series, trades, interval: str) -> dict:
    if len(equity) < 2:
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "trade_count": len(trades),
            "turnover": 0.0,
        }
    rets = _returns(equity)
    periods = periods_per_year(interval)  # type: ignore[arg-type]
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    years = len(equity) / periods
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0
    std = float(rets.std(ddof=0))
    sharpe = float(rets.mean() / std * math.sqrt(periods)) if std > 0 else 0.0
    running_max = equity.cummax()
    drawdown = float((1 - equity / running_max).max())
    pnls = [float(t.pnl) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": drawdown,
        "win_rate": (wins / len(pnls)) if pnls else 0.0,
        "trade_count": len(pnls),
        "turnover": float(np.sum([abs(p) for p in pnls])) / float(equity.iloc[0]),
    }
