"""报告生成（QuantStats）。§3.9。

年化因子必须显式传：日频 365、小时 8760、分钟 525600。
QuantStats 默认按 252（交易日）算，用在 7×24 的加密市场会把年化收益放大 1.45 倍。
无风险利率显式传 0，不要依赖默认值。
"""

from __future__ import annotations

from enum import StrEnum

PERIODS_PER_YEAR: dict[str, int] = {
    "1m": 525_600,
    "5m": 105_120,
    "15m": 35_040,
    "1h": 8_760,
    "4h": 2_190,
    "1d": 365,
}


class Interval(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


def periods_per_year(interval: Interval | str) -> int:
    """显式换算，禁止调用方自己猜。接受枚举或原始字符串。

    接受 str 是为了让 research.metrics 这类只拿到 interval 字符串的调用方
    不必先构造枚举；两个类型入口保持同一张表，避免出现第二份年化因子。
    """
    key = interval.value if isinstance(interval, Interval) else str(interval)
    try:
        return PERIODS_PER_YEAR[key]
    except KeyError as exc:
        raise ValueError(f"unsupported interval: {key}") from exc


def build_report(returns, interval: str) -> dict:
    """把日/小时收益序列变成报告字典。返回数据，不返回 HTML。"""
    import numpy as np
    import pandas as pd

    try:
        periods = PERIODS_PER_YEAR[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported interval: {interval}") from exc
    series = pd.Series(returns).dropna()
    if series.empty:
        return {
            "cagr": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "volatility": 0.0,
            "curve": [],
            "periods_per_year": periods,
            "disclaimer": "模拟回测，非实盘业绩",
        }
    equity = (1.0 + series).cumprod()
    years = len(series) / periods
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0
    vol = float(series.std(ddof=0) * np.sqrt(periods))
    sharpe = (
        float(series.mean() / series.std(ddof=0) * np.sqrt(periods)) if series.std(ddof=0) else 0.0
    )
    downside = series[series < 0].std(ddof=0)
    sortino = float(series.mean() / downside * np.sqrt(periods)) if downside else 0.0
    drawdown = float((1 - equity / equity.cummax()).max())
    calmar = float(cagr / drawdown) if drawdown > 0 else 0.0
    return {
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": drawdown,
        "calmar": calmar,
        "volatility": vol,
        "periods_per_year": periods,
        "curve": [float(v) for v in equity],
        "disclaimer": "模拟回测，非实盘业绩",
    }
