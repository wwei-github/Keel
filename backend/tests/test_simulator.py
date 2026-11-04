"""逐 bar 执行器：成交在下一根开盘、滑点方向正确、无未来数据。

这里用「按时间戳出信号」的假策略而不是按计数器，因为计数器会掩盖一个真实错误：
运行时会跳过历史不足的前几根 bar，计数器与 bar 序号就此错位，测试却还是绿的。
"""

from __future__ import annotations

import pandas as pd
import pytest

from keel.contracts import CostModel, Direction, Signal
from keel.research.simulator import simulate

TS = pd.date_range("2023-11-01", periods=5, freq="1h", tz="UTC")


def _bars(closes: list[float], *, opens: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    opens = opens or closes
    return pd.DataFrame(
        {
            "open_time": TS[:n],
            "symbol": "BTC/USDT",
            "interval": "1h",
            "open": opens[:n],
            "high": [max(o, c) * 1.001 for o, c in zip(opens, closes, strict=True)],
            "low": [min(o, c) * 0.999 for o, c in zip(opens, closes, strict=True)],
            "close": closes,
            "volume": 1.0,
            "revision": 1,
            "ingested_at": pd.Timestamp("2023-12-01T00:00:00Z"),
        }
    )


class AtTimes:
    """在指定时间出信号。信号只表达意图，成交由执行器决定。"""

    lookback = 1
    strategy_key = "at_times"
    strategy_version = 1
    params_schema: dict = {}

    def __init__(self, enter_at: pd.Timestamp, exit_at: pd.Timestamp) -> None:
        self.enter_at, self.exit_at = enter_at, exit_at

    def required_lookback(self, params: dict) -> int:
        return 1

    def decide(self, window, bar, params, position, state):
        stamp = pd.Timestamp(bar.open_time)
        if stamp == self.enter_at and position.direction is Direction.FLAT:
            return [Signal(bar.open_time, Direction.LONG, "test", "feat-v1", "enter")]
        if stamp == self.exit_at and position.direction is Direction.LONG:
            return [Signal(bar.open_time, Direction.FLAT, "test", "feat-v1", "exit")]
        return []


COST = CostModel(
    cost_model_id="t",
    fee_rate=0.001,
    slippage_bps=10.0,
    min_notional=5.0,
    funding_included=False,
)
BARS = _bars([100.0, 105.0, 110.0, 115.0, 120.0], opens=[100.0, 110.0, 120.0, 130.0, 140.0])


def test_fill_price_is_next_open_with_slippage() -> None:
    sim = simulate(BARS, AtTimes(TS[1], TS[3]), {}, COST, initial_cash=1000.0)
    assert len(sim.trades) == 1
    trade = sim.trades[0]
    # TS[1] 出信号 → TS[2] 开盘 120 成交（+10bps）；TS[3] 出平仓信号 → TS[4] 开盘 140 成交（-10bps）
    assert trade.entry_price == pytest.approx(120.0 * 1.001)
    assert trade.exit_price == pytest.approx(140.0 * 0.999)
    assert trade.entry_time == TS[2] and trade.exit_time == TS[4]


def test_fees_are_charged_on_both_sides() -> None:
    sim = simulate(BARS, AtTimes(TS[1], TS[3]), {}, COST, initial_cash=1000.0)
    trade = sim.trades[0]
    expected = trade.entry_price * trade.qty * 0.001 + trade.exit_price * trade.qty * 0.001
    assert trade.fees == pytest.approx(expected)


def test_no_trades_keeps_equity_flat() -> None:
    bars = _bars([100.0] * 5)
    sim = simulate(bars, AtTimes(TS[0], TS[0]), {}, COST, initial_cash=1000.0)
    assert sim.trades == ()
    assert sim.equity.iloc[-1] == pytest.approx(1000.0)


def test_equity_length_matches_bars() -> None:
    sim = simulate(BARS, AtTimes(TS[1], TS[2]), {}, COST)
    assert len(sim.equity) == len(BARS)
    assert sim.equity.index.equals(pd.DatetimeIndex(BARS["open_time"]))


def test_min_notional_blocks_tiny_order() -> None:
    sim = simulate(_bars([1.0] * 5), AtTimes(TS[1], TS[3]), {}, COST, initial_cash=1.0)
    assert sim.trades == ()  # 1 USDT 连最小下单额 5 USDT 都不够


def test_position_is_closed_at_end_of_data() -> None:
    sim = simulate(
        BARS, AtTimes(TS[1], pd.Timestamp("2030-01-01", tz="UTC")), {}, COST, initial_cash=1000.0
    )
    assert len(sim.trades) == 1
    # 数据末尾强制平仓，按最后一根收盘价
    assert sim.trades[0].exit_price == pytest.approx(120.0 * 0.999)


def test_exposure_is_bounded_by_one() -> None:
    sim = simulate(BARS, AtTimes(TS[1], TS[3]), {}, COST, initial_cash=1000.0)
    assert sim.exposure.max() <= 1.0
    assert sim.exposure.min() >= 0.0
