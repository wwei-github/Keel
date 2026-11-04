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


def periods_per_year(interval: Interval) -> int:
    """显式换算，禁止调用方自己猜。"""
    return PERIODS_PER_YEAR[interval.value]


def build_report(returns, interval: Interval) -> dict:
    """生成指标字典与曲线序列（返回数据，不返回 HTML）。"""
    raise NotImplementedError("报告生成尚未实现：等待回测产出 equity curve")
