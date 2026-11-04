"""Keel 策略契约（对应架构文档 §3.7）。

这二十来行代码就是文档 §3.7 那张表的可执行版本。文档里写的签名是**草案**，
这里的 ``Strategy`` Protocol 加上 ``tests/test_contract.py`` 跑通，才算契约冻结（A9）。

契约六项与代码的对应关系：

==================  ==========================================
文档契约项            代码落点
==================  ==========================================
信号函数              ``Strategy.decide`` 的签名
输入窗口              ``Strategy.lookback`` + ``slice_window``
参数 schema           ``Strategy.params_schema``
特征入口              ``keel.features``（策略内不得自行算指标）
成本模型              ``CostModel``（对应表 ``cost_models``）
输出登记              ``Signal`` 的必填字段
==================  ==========================================

运行时的强制约束（由 ``run_decide`` 施加，不靠策略自觉）：

1. 窗口是**历史**，不含触发 bar；触发 bar 单独以 ``bar`` 参数给出。
   两者分开，是为了让「哪根 bar 可以用来算指标」在签名上就没有歧义。
2. 输出必须是 ``list[Signal]``，空列表表示无信号 —— 不用 ``None``。
3. 每条信号的 ``bar_open_time`` 必须等于触发 bar 的时间，``feature_version``
   必须等于本次运行的版本，方向必须在允许集合内。违反即拒绝，不静默纠正。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import pandas as pd

__all__ = [
    "Direction",
    "BarView",
    "Signal",
    "PositionView",
    "CostModel",
    "Strategy",
    "InsufficientHistory",
    "ParamExceedsWindow",
    "ContractViolation",
    "slice_window",
    "run_decide",
]


class ContractViolation(ValueError):
    """策略返回的东西不满足契约。运行时应当拒绝，而不是猜策略的意思。"""


class InsufficientHistory(ValueError):
    """历史不足 ``lookback`` 根，无法构造窗口。调用方应跳过这根 bar，不算错误。"""


class ParamExceedsWindow(ContractViolation):
    """参数要求的窗口比策略声明的 ``lookback`` 更长。

    **这是修订 ⑥ 发现的真实缺陷。** 初稿的六项契约里，``lookback`` 是一个
    静态类属性，而参数取值上限写在 ``params_schema`` 里，两者互不知情。
    于是 ``SmaCross(lookback=61)`` 配上 ``{"slow": 90}`` 会通过 schema 校验
    （schema 写的上限是 120），但运行时永远拿不到 90 根历史 —— 结果是
    **一根信号都不出，也不报错**。

    静默返回空列表是最坏的失败形态：没有异常、没有日志、没有告警，
    只有「这个策略好像不太行」的错觉。所以参数超出窗口必须在第一次
    决策时立刻抛错，让配置错误在提交阶段就暴露。
    """


class Direction(StrEnum):
    """目标方向。

    现货范围（V1–V2）只允许 ``LONG`` 与 ``FLAT``：没有可借券，做空表达不了。
    ``SHORT`` 留给 V3 引入永续后用；先定义是为了让运行时能明确**拒绝**它，
    而不是等策略写出一个 ``"short"`` 字符串然后在网关里炸掉。
    """

    LONG = "long"
    FLAT = "flat"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class BarView:
    """触发 bar（当前这根、已收盘）。

    **为什么要单独给出、而不是让策略从窗口里取。** 初稿的契约只给一个
    ``window = bars[-(lookback+1):-1]``，让「当前 bar 只用于触发，不进入窗口」。
    写代码时立刻暴露问题：策略拿不到当前收盘价，就没法判断「收盘价是否上破
    唐奇安上轨」——这正是突破策略的核心判断。于是每个策略都得自己从
    别处摸当前价，或者干脆把窗口改成含当前 bar（引入未来数据）。

    正确的切法是：**窗口是历史（用来算指标），bar 是决策点（用来判断）**。
    两者在签名上分开，策略就没有「该不该包含当前 bar」的模糊空间了。
    """

    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None:
            raise ContractViolation("bar.open_time 必须带时区（全系统 UTC，§2.5.8）")


@dataclass(frozen=True, slots=True)
class Signal:
    """一条信号。字段即「输出登记」契约项（§3.7 表格最后一行）。

    ``qty`` 故意不在这里：仓位大小是风控职责（§3.6），策略只表达方向与理由。
    策略如果能决定数量，风控的仓位上限就只剩事后裁剪，两处逻辑必然分叉。
    """

    bar_open_time: datetime
    direction: Direction
    trigger_type: str
    feature_version: str
    reason: str = ""

    def __post_init__(self) -> None:
        if self.bar_open_time.tzinfo is None:
            raise ContractViolation("bar_open_time 必须是带时区的时间（全系统 UTC，§2.5.8）")
        if not self.trigger_type:
            raise ContractViolation("trigger_type 不能为空")
        if not self.feature_version:
            raise ContractViolation("feature_version 不能为空，否则回测无法复现")


@dataclass(frozen=True, slots=True)
class PositionView:
    """策略能看到的持仓视图 —— **只有当前标的**。

    这是 §3.7 修订 ② 的落点。全账户敞口是风控职责；策略看得到全账户，
    就会自己算总杠杆、自己判断「这个方向已经太重了」，两处逻辑必然分叉。

    ``instrument_id`` 不是给策略用的，是让运行时能断言
    「我给出去的确实是你正在决策的那个标的」。
    """

    instrument_id: str
    direction: Direction = Direction.FLAT
    qty: float = 0.0
    entry_price: float | None = None
    unrealized_pnl: float = 0.0
    holding_bars: int = 0

    @property
    def is_flat(self) -> bool:
        return self.direction is Direction.FLAT or self.qty == 0.0


@dataclass(frozen=True, slots=True)
class CostModel:
    """成本模型（对应表 ``cost_models``，§3.7 修订 ④）。

    从策略参数里拆出来的理由：这是回测与实盘共用的常量，分散在各策略配置里
    必然漂移，而漂移后无法判断该信哪个。
    """

    cost_model_id: str
    fee_rate: float
    slippage_bps: float
    min_notional: float
    funding_included: bool = False

    def __post_init__(self) -> None:
        if self.fee_rate < 0 or self.slippage_bps < 0:
            raise ContractViolation("费率与滑点不能为负")
        if self.min_notional < 0:
            raise ContractViolation("min_notional 不能为负")


@runtime_checkable
class Strategy(Protocol):
    """策略契约。六项里的四项在这里（签名、窗口、schema、特征入口）。

    实现者只需满足这个结构，不必继承基类 —— 单人项目里，
    继承层次带来的耦合大于复用。
    """

    #: 输入窗口长度（bar 根数）。运行时按 ``bars[-(lookback + 1):-1]`` 截断注入，
    #: 即触发 bar 之前的 ``lookback`` 根，**不含触发 bar**。
    lookback: int

    #: 参数 JSON Schema。提交回测时由 API 层校验，策略内不做参数校验。
    params_schema: Mapping[str, Any]

    def required_lookback(self, params: Mapping[str, Any]) -> int:
        """这组参数实际需要多少根历史。

        ``lookback`` 是**上界**（运行时按它预取数据），这个方法是**实际需要量**。
        两者分开是因为预取必须在知道参数之前完成，而参数会改变需求。
        运行时断言 ``required_lookback(params) <= lookback``，
        不一致即抛 ``ParamExceedsWindow`` —— 不允许静默少给数据。
        """
        ...

    def decide(
        self,
        window: pd.DataFrame,
        bar: BarView,
        params: Mapping[str, Any],
        position: PositionView,
        state: dict[str, Any],
    ) -> list[Signal]:
        """对一根已收盘 bar 做决策。

        约定：
        - ``window`` 是触发 bar 之前的历史，已按 ``lookback`` 截断，不含触发 bar。
        - ``bar`` 是触发 bar 本身（已收盘），决策点就是它。
        - 返回 ``list[Signal]``；无信号返回 ``[]``，**不要返回 None**。
        - 不得做 I/O、不得读时钟、不得持有进程级全局变量。
        - ``state`` 是唯一允许的可变载体，由运行时持久化与回放（修订 ⑤）。
        """
        ...


def slice_window(bars: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """切出策略窗口：触发 bar 之前 ``lookback`` 根，**不含触发 bar**。

    ``bars`` 必须把触发 bar 放在最后一行。那个 ``-1`` 就是防未来数据的地方。
    这是「最容易写错、且写错了不报错」的一类 bug，所以由运行时强制，
    不留给每个策略自己 ``iloc``。
    """
    if lookback < 1:
        raise ValueError(f"lookback 必须 >= 1，收到 {lookback}")
    if len(bars) < lookback + 1:
        raise InsufficientHistory(
            f"需要至少 {lookback + 1} 根 bar（窗口 {lookback} + 触发 bar），实际 {len(bars)}"
        )
    return bars.iloc[-(lookback + 1) : -1]


def check_window_fits(strategy: Strategy, params: Mapping[str, Any]) -> int:
    """断言参数所需的窗口不超过策略声明的 ``lookback``。返回实际需要量。

    没实现 ``required_lookback`` 的策略退回静态 ``lookback``（等价于旧行为），
    但那样的策略无法在提交阶段发现「参数比窗口长」的问题。
    """
    declared = int(getattr(strategy, "lookback", 0))
    fn = getattr(strategy, "required_lookback", None)
    required = int(fn(params)) if callable(fn) else declared

    if required > declared:
        raise ParamExceedsWindow(
            f"参数要求 {required} 根历史，但 {type(strategy).__name__} 声明的 lookback 只有 "
            f"{declared}。要么调小参数，要么把它构造为 lookback>={required}。"
            f"（放过去的话，这个策略会永远不出信号且不报错）"
        )
    return required


def make_bar_view(bars: pd.DataFrame) -> BarView:
    """把 ``bars`` 的最后一行转成 ``BarView``。"""
    last = bars.iloc[-1]
    open_time = bars.index[-1]
    if not isinstance(open_time, pd.Timestamp):
        raise ContractViolation("bars 的索引必须是时间")
    if open_time.tzinfo is None:
        raise ContractViolation("bars 的索引必须带时区（全系统 UTC，§2.5.8）")
    return BarView(
        open_time=open_time.to_pydatetime(),
        open=float(last["open"]),
        high=float(last["high"]),
        low=float(last["low"]),
        close=float(last["close"]),
        volume=float(last["volume"]),
    )


def run_decide(
    strategy: Strategy,
    bars: pd.DataFrame,
    params: Mapping[str, Any],
    position: PositionView,
    state: dict[str, Any] | None = None,
    *,
    feature_version: str,
    allowed_directions: Sequence[Direction] = (Direction.LONG, Direction.FLAT),
) -> list[Signal]:
    """运行时调用策略的唯一入口：切窗口、造 BarView、调策略、校验输出。

    校验失败直接抛 ``ContractViolation``，不静默纠正 —— 契约的价值在于
    「违反时立刻可见」。一旦运行时开始猜策略的意图，契约就名存实亡。
    """
    if len(bars) == 0:
        raise InsufficientHistory("bars 为空")

    # 配置错误优先于数据不足：先确认「这组参数配得上这个窗口」，
    # 再谈「历史够不够」。顺序反了的话，窗口不足会先抛 InsufficientHistory，
    # 把真正的配置问题盖住。
    check_window_fits(strategy, params)

    bar = make_bar_view(bars)
    window = slice_window(bars, strategy.lookback)
    state = state if state is not None else {}

    out = strategy.decide(window, bar, params, position, state)

    if not isinstance(out, list):
        raise ContractViolation(
            f"decide 必须返回 list[Signal]，收到 {type(out).__name__}（修订 ①）"
        )

    for i, sig in enumerate(out):
        if not isinstance(sig, Signal):
            raise ContractViolation(f"第 {i} 条不是 Signal，是 {type(sig).__name__}")
        if sig.bar_open_time != bar.open_time:
            raise ContractViolation(
                f"第 {i} 条信号的 bar_open_time={sig.bar_open_time} 与触发 bar "
                f"{bar.open_time} 不一致"
            )
        if sig.feature_version != feature_version:
            raise ContractViolation(
                f"第 {i} 条信号 feature_version={sig.feature_version} 与本次运行 "
                f"{feature_version} 不一致"
            )
        if sig.direction not in allowed_directions:
            raise ContractViolation(
                f"第 {i} 条信号方向 {sig.direction} 不在允许集合 "
                f"{[d.value for d in allowed_directions]} 内（现货不能做空）"
            )

    return out
