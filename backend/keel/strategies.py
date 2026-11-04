"""两个玩具策略。它们的作用不是赚钱，是**证伪契约**。

契约是否够用只能靠代码验证（§3.7 结尾）。选这两个是因为它们覆盖两种
截然不同的形态：

- ``SmaCross``：连续型，只需要窗口与参数，几乎无状态。
- ``DonchianBreakout``：事件型，需要跨 bar 记住「本轮是否已进场」，
  正好压测修订 ⑤ 的 ``state``。

如果契约能同时容纳这两种，它大概能容纳大部分真实策略。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from keel.contracts import (
    BarView,
    ContractViolation,
    Direction,
    PositionView,
    Signal,
)
from keel.features import FEATURE_VERSION, donchian, sma
from keel.research.registry import register

__all__ = ["SmaCross", "DonchianBreakout"]


def _extend(window: pd.DataFrame, bar: BarView) -> pd.DataFrame:
    """把触发 bar 接到窗口末尾，返回含触发 bar 的序列。

    指标必须基于「含触发 bar」的序列计算（否则唐奇安突破永远不成立），
    但**窗口本身不含触发 bar** —— 这是契约的最重要一条。
    所有策略都用同一个方式扩展，避免有人图省事自己 ``.append`` 出未来数据。
    """
    row = pd.DataFrame(
        {
            "open": [bar.open],
            "high": [bar.high],
            "low": [bar.low],
            "close": [bar.close],
            "volume": [bar.volume],
        },
        index=pd.DatetimeIndex([pd.Timestamp(bar.open_time)]),
    )
    return pd.concat([window, row])


@dataclass
class SmaCross:
    """双均线交叉。

    规则：快线上穿慢线 → 目标 ``LONG``；下穿 → 目标 ``FLAT``。
    只在「方向需要变化」时出信号，同一方向重复触发不重复下单。
    """

    lookback: int = 61
    strategy_key = "sma_cross"
    strategy_version = 1
    params_schema: Mapping[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "fast": {"type": "integer", "minimum": 2, "maximum": 59, "default": 20},
                # 上限由 lookback 决定，默认 61 时 slow 最大 60。
                # 想用更长的慢线，就把 SmaCross(lookback=N) 一起调大 —— 两者必须一致。
                "slow": {"type": "integer", "minimum": 3, "maximum": 120, "default": 60},
            },
            "required": ["fast", "slow"],
            "additionalProperties": False,
        }
    )

    def required_lookback(self, params: Mapping[str, Any]) -> int:
        """慢线长度即实际需要的历史根数。

        修订 ⑥：``lookback`` 只是上界，真实需求随参数变化。
        不声明这一项，``slow=90`` 配 ``lookback=61`` 会通过 schema 校验、
        然后永远不出信号也不报错。
        """
        return int(params["slow"])

    def decide(
        self,
        window: pd.DataFrame,
        bar: BarView,
        params: Mapping[str, Any],
        position: PositionView,
        state: dict[str, Any],
    ) -> list[Signal]:
        fast = int(params["fast"])
        slow = int(params["slow"])
        if fast >= slow:
            raise ContractViolation(f"fast({fast}) 必须小于 slow({slow})")

        ext = _extend(window, bar)
        if len(ext) < slow + 1:
            return []

        close = ext["close"]
        # 用 keel.features 的原语，不在策略里自己 rolling —— 契约要求。
        fast_series = sma(close, fast)
        slow_series = sma(close, slow)

        vals = [
            fast_series.iloc[-2],
            slow_series.iloc[-2],
            fast_series.iloc[-1],
            slow_series.iloc[-1],
        ]
        if any(pd.isna(v) for v in vals):
            return []

        prev_diff = fast_series.iloc[-2] - slow_series.iloc[-2]
        curr_diff = fast_series.iloc[-1] - slow_series.iloc[-1]

        target: Direction | None = None
        trigger = ""
        if prev_diff <= 0 < curr_diff:
            target, trigger = Direction.LONG, "sma_cross_up"
        elif prev_diff >= 0 > curr_diff:
            target, trigger = Direction.FLAT, "sma_cross_down"

        if target is None or target is position.direction:
            return []

        return [
            Signal(
                bar_open_time=bar.open_time,
                direction=target,
                trigger_type=trigger,
                feature_version=FEATURE_VERSION,
                reason=f"fast={fast} slow={slow} prev={prev_diff:.8f} curr={curr_diff:.8f}",
            )
        ]


@dataclass
class DonchianBreakout:
    """唐奇安通道突破。

    规则：收盘价上破前 N 根最高 → ``LONG``；跌破前 N 根最低 → ``FLAT``。

    这个策略**需要实例级状态**：它要记住「本轮已经因为突破进过场」，
    否则价格在通道上沿反复震荡时会连续出信号。``state`` 正是为此存在（修订 ⑤）。
    如果契约强制纯函数，这个记录只能塞进闭包或全局变量，两者都不可回放。
    """

    lookback: int = 21
    strategy_key = "donchian_breakout"
    strategy_version = 1
    params_schema: Mapping[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "channel": {"type": "integer", "minimum": 5, "maximum": 60, "default": 20},
            },
            "required": ["channel"],
            "additionalProperties": False,
        }
    )

    def required_lookback(self, params: Mapping[str, Any]) -> int:
        """通道长度 + 1：``donchian`` 内部 shift(1)，要多一根才能出第一个有效值。"""
        return int(params["channel"]) + 1

    def decide(
        self,
        window: pd.DataFrame,
        bar: BarView,
        params: Mapping[str, Any],
        position: PositionView,
        state: dict[str, Any],
    ) -> list[Signal]:
        channel = int(params["channel"])
        if channel < 2:
            raise ContractViolation(f"channel 必须 >= 2，收到 {channel}")

        ext = _extend(window, bar)
        upper, lower = donchian(ext["high"], ext["low"], channel)

        # donchian 内部已 shift(1)，所以这里的 iloc[-1] 是
        # 「触发 bar 之前 channel 根的最高/最低」，不含触发 bar 自己。
        if pd.isna(upper.iloc[-1]) or pd.isna(lower.iloc[-1]):
            return []

        up_level = float(upper.iloc[-1])
        dn_level = float(lower.iloc[-1])

        # state 是唯一允许的可变载体（修订 ⑤）。运行时负责持久化与回放。
        entered_this_leg = bool(state.get("entered_this_leg", False))

        if bar.close > up_level:
            if position.direction is Direction.LONG or entered_this_leg:
                return []
            state["entered_this_leg"] = True
            state["leg_start"] = bar.open_time.isoformat()
            return [
                Signal(
                    bar_open_time=bar.open_time,
                    direction=Direction.LONG,
                    trigger_type="donchian_breakout_up",
                    feature_version=FEATURE_VERSION,
                    reason=f"close={bar.close:.8f} > upper={up_level:.8f}",
                )
            ]

        if bar.close < dn_level:
            if position.direction is Direction.FLAT:
                return []
            state["entered_this_leg"] = False
            return [
                Signal(
                    bar_open_time=bar.open_time,
                    direction=Direction.FLAT,
                    trigger_type="donchian_breakout_down",
                    feature_version=FEATURE_VERSION,
                    reason=f"close={bar.close:.8f} < lower={dn_level:.8f}",
                )
            ]

        return []


register(SmaCross)
register(DonchianBreakout)
