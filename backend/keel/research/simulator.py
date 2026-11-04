"""逐 bar 执行器（engine=keel-bar，见计划 D2）。

三条不能改的规则：
1. **成交发生在下一根 bar 的开盘价**。用当前 bar 收盘价成交等于偷看未来——
   这是回测最常见也最难自查的偏差，所以它写在校验里而不是文档里。
2. 滑点方向永远不利于自己：买入加、卖出减。
3. 一切窗口构造都走 ``run_decide``，不在这里手写 ``iloc`` —— 契约的执行只有一个地方，
   执行器自己切一次窗口，早晚会和契约里的那次算出不同结果。

这里只做多头（V1–V2 现货，ADR-001）。做空要等永续，那是另一套保证金模型。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from keel.contracts import (
    CostModel,
    Direction,
    InsufficientHistory,
    PositionView,
    run_decide,
)

BPS = 1e-4


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    entry_time: pd.Timestamp | None
    exit_time: pd.Timestamp | None
    direction: str
    qty: float
    entry_price: float
    exit_price: float | None
    pnl: float
    fees: float


@dataclass(frozen=True, slots=True)
class Simulation:
    trades: tuple[Trade, ...]
    equity: pd.Series
    exposure: pd.Series


def _fill(price: float, side: str, slippage_bps: float) -> float:
    slip = price * slippage_bps * BPS
    return price + slip if side == "buy" else price - slip


def simulate(
    bars: pd.DataFrame,
    strategy,
    params: dict,
    cost: CostModel,
    *,
    initial_cash: float = 10_000.0,
    state: dict | None = None,
    feature_version: str = "feat-v1",
) -> Simulation:
    if bars.empty:
        empty = pd.Series(dtype="float64")
        return Simulation((), empty, empty)

    frame = bars.reset_index(drop=True).sort_values("open_time").reset_index(drop=True)
    symbol = str(frame["symbol"].iloc[0])
    state = state if state is not None else {}
    cash = float(initial_cash)
    qty = 0.0
    entry_price: float | None = None
    entry_time: pd.Timestamp | None = None
    entry_index = 0
    entry_fees = 0.0
    trades: list[Trade] = []
    # 下一根开盘要执行的动作队列。用队列而不是单个槽位：
    # 同一根 bar 出「平旧仓 + 开新仓」是常见的，一个槽位会静默丢掉第二条。
    pending: list[Direction] = []
    equity: list[float] = []
    exposure: list[float] = []

    def close_position(price: float, when: pd.Timestamp) -> None:
        nonlocal cash, qty, entry_price, entry_time, entry_fees
        if qty == 0.0 or entry_price is None:
            return
        fill = _fill(price, "sell", float(cost.slippage_bps))
        fee = fill * qty * float(cost.fee_rate)
        cash += fill * qty - fee
        fees = entry_fees + fee
        trades.append(
            Trade(
                symbol,
                entry_time,
                when,
                "LONG",
                qty,
                entry_price,
                fill,
                (fill - entry_price) * qty - fees,
                fees,
            )
        )
        qty, entry_price, entry_time, entry_fees = 0.0, None, None, 0.0

    for i in range(len(frame)):
        row = frame.iloc[i]
        ts = pd.Timestamp(row["open_time"])

        while pending:
            direction = pending.pop(0)
            if direction is Direction.LONG and qty == 0.0:
                fill = _fill(float(row["open"]), "buy", float(cost.slippage_bps))
                # 分母含手续费：按 cash/fill 算会多买一手，cash 变负、敞口算出来 > 1
                size = cash / (fill * (1.0 + float(cost.fee_rate)))
                if size > 0 and fill * size >= float(cost.min_notional):
                    fee = fill * size * float(cost.fee_rate)
                    qty, entry_price, entry_time, entry_fees = size, fill, ts, fee
                    entry_index = i
                    cash -= fill * size + fee
            elif direction is Direction.FLAT and qty > 0.0:
                close_position(float(row["open"]), ts)

        mark = float(row["close"])
        equity.append(cash + qty * mark)
        # 现货不能加杠杆，敞口上限就是 1；clamp 只为吸收浮点误差
        exposure.append(min(1.0, (qty * mark) / (cash + qty * mark)) if qty else 0.0)

        position = PositionView(
            instrument_id=symbol,
            direction=Direction.LONG if qty else Direction.FLAT,
            qty=qty,
            entry_price=entry_price,
            unrealized_pnl=(mark - entry_price) * qty if entry_price else 0.0,
            holding_bars=(i - entry_index) if qty else 0,
        )
        # 索引必须是时间：make_bar_view 会拒绝无时区的索引。
        stamped = frame.iloc[: i + 1].set_index("open_time")
        try:
            signals = run_decide(
                strategy, stamped, params, position, state, feature_version=feature_version
            )
        except InsufficientHistory:
            # 历史不足不是错误，跳过这根 bar（契约文档明说了这一点）。
            continue
        for signal in signals:
            pending.append(signal.direction)

    if qty and len(frame):
        close_position(float(frame["close"].iloc[-1]), pd.Timestamp(frame["open_time"].iloc[-1]))

    index = pd.DatetimeIndex(frame["open_time"])
    return Simulation(
        tuple(trades),
        pd.Series(equity, index=index),
        pd.Series(exposure, index=index),
    )
