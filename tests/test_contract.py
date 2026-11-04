# -*- coding: utf-8 -*-
"""契约验证测试（A9 的判定依据）。

这个文件跑通，契约才算冻结。它测的不是策略赚不赚钱，而是六项契约项
**是否真的被运行时强制**：

1. **窗口不含触发 bar**（修订 ③）—— 未来数据是回测的头号杀手，
   且写错了不会报错，只会让回测好看得可疑。
2. **返回 list 而非 None**（修订 ①）—— 反手那根 bar 要能表达两条信号。
3. **position 只有当前标的**（修订 ②）—— 全账户敞口是风控职责。
4. **成本模型独立**（修订 ④）—— 结构上就测。
5. **state 可持久化可回放**（修订 ⑤）—— 这是本文件最有价值的一条：
   从任意检查点恢复状态重放，后续信号必须逐条相同。做不到，
   就说明策略把状态藏在了契约管不到的地方。
6. **特征无未来依赖** —— ``compute(bars[:k])`` 必须等于 ``compute(bars)[:k]``。

运行：``uv run pytest``
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import pytest
from hypothesis import given, settings, strategies as st

from keel.contracts import (
    BarView,
    ContractViolation,
    ParamExceedsWindow,
    check_window_fits,
    CostModel,
    Direction,
    InsufficientHistory,
    PositionView,
    Signal,
    Strategy,
    make_bar_view,
    run_decide,
    slice_window,
)
from keel.features import FEATURE_VERSION, compute, donchian, sma
from keel.strategies import DonchianBreakout, SmaCross

UTC = "UTC"
INSTRUMENT = "BTC/USDT"

# --------------------------------------------------------------------------
# 测试数据构造
# --------------------------------------------------------------------------


def make_bars(closes: list[float], start: str = "2024-01-01", freq: str = "1h") -> pd.DataFrame:
    """由收盘价序列造一份合法的 OHLCV。high/low 由 close 确定性派生。"""
    index = pd.date_range(start, periods=len(closes), freq=freq, tz=UTC)
    close = pd.Series(closes, index=index, dtype="float64")
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": pd.Series(1.0, index=index),
        }
    )


def trend_bars(n: int = 300, start_price: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """单边上涨，用来保证双均线会出金叉。"""
    closes = [start_price + i * step for i in range(n)]
    return make_bars(closes)


def wave_bars(n: int = 300, period: int = 40, amp: float = 5.0) -> pd.DataFrame:
    """正弦波，用来触发唐奇安突破与回落。"""
    import math

    closes = [100.0 + amp * math.sin(2 * math.pi * i / period) for i in range(n)]
    return make_bars(closes)


def flat_position() -> PositionView:
    return PositionView(instrument_id=INSTRUMENT, direction=Direction.FLAT, qty=0.0)


def long_position() -> PositionView:
    return PositionView(
        instrument_id=INSTRUMENT, direction=Direction.LONG, qty=1.0, entry_price=100.0
    )


# --------------------------------------------------------------------------
# hypothesis 生成器
# --------------------------------------------------------------------------

price_lists = st.lists(
    st.floats(min_value=1.0, max_value=10_000.0, allow_nan=False, allow_infinity=False),
    min_size=3,
    max_size=40,
)


# --------------------------------------------------------------------------
# 契约 1：窗口不含触发 bar（修订 ③）
# --------------------------------------------------------------------------


@given(price_lists, st.integers(min_value=1, max_value=10))
@settings(max_examples=200, deadline=None)
def test_slice_window_excludes_trigger_bar(closes: list[float], lookback: int) -> None:
    """窗口长度恒等于 lookback，且最后一根严格早于触发 bar。"""
    if len(closes) < lookback + 1:
        with pytest.raises(InsufficientHistory):
            slice_window(make_bars(closes), lookback)
        return

    bars = make_bars(closes)
    window = slice_window(bars, lookback)

    assert len(window) == lookback
    # window 是 bars[-(lookback+1):-1]，所以它的末根是 bars 的倒数第二根
    assert window.index[-1] == bars.index[-2]
    # 核心断言：窗口里不存在触发 bar 的时间
    assert bars.index[-1] not in window.index
    assert (window.index < bars.index[-1]).all()


@given(price_lists)
@settings(max_examples=100, deadline=None)
def test_slice_window_rejects_bad_lookback(closes: list[float]) -> None:
    bars = make_bars(closes)
    for bad in (0, -1, -100):
        with pytest.raises(ValueError):
            slice_window(bars, bad)


def test_slice_window_needs_one_extra_bar() -> None:
    """lookback 根不够，必须多一根触发 bar。差一根就要报错，不能悄悄少算。"""
    bars = make_bars([100.0] * 20)
    assert len(slice_window(bars, 19)) == 19
    with pytest.raises(InsufficientHistory):
        slice_window(bars, 20)


class _SpyStrategy:
    """记录它到底看到了什么。用来证明运行时没有偷偷塞未来数据。"""

    lookback = 10
    params_schema: Mapping[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.seen_window_times: list[pd.Timestamp] = []
        self.seen_bar_time: pd.Timestamp | None = None
        self.calls = 0

    def decide(self, window, bar, params, position, state):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.seen_window_times = list(window.index)
        self.seen_bar_time = pd.Timestamp(bar.open_time)
        return []


def test_strategy_sees_no_data_after_trigger_bar() -> None:
    """策略看到的最大时间戳就是触发 bar 的时间，一根都不能多。"""
    bars = make_bars([100.0 + i for i in range(100)])
    spy = _SpyStrategy()

    run_decide(spy, bars, {}, flat_position(), {}, feature_version=FEATURE_VERSION)

    assert spy.calls == 1
    assert spy.seen_bar_time == bars.index[-1]
    assert max(spy.seen_window_times) < bars.index[-1]
    assert len(spy.seen_window_times) == spy.lookback


# --------------------------------------------------------------------------
# 契约 2：返回 list 而非 None（修订 ①）
# --------------------------------------------------------------------------


class _ReturnsNone:
    lookback = 5
    params_schema: Mapping[str, Any] = {"type": "object", "properties": {}}

    def decide(self, window, bar, params, position, state):  # type: ignore[no-untyped-def]
        return None  # type: ignore[return-value]


class _ReturnsSingleSignal:
    lookback = 5
    params_schema: Mapping[str, Any] = {"type": "object", "properties": {}}

    def decide(self, window, bar, params, position, state):  # type: ignore[no-untyped-def]
        return Signal(
            bar_open_time=bar.open_time,
            direction=Direction.LONG,
            trigger_type="x",
            feature_version=FEATURE_VERSION,
        )


def test_none_return_is_rejected() -> None:
    bars = make_bars([100.0] * 20)
    with pytest.raises(ContractViolation, match="list\\[Signal\\]"):
        run_decide(_ReturnsNone(), bars, {}, flat_position(), {}, feature_version=FEATURE_VERSION)


def test_bare_signal_is_rejected() -> None:
    """单条 Signal 也不行 —— 反手那根 bar 需要能表达两条。"""
    bars = make_bars([100.0] * 20)
    with pytest.raises(ContractViolation):
        run_decide(
            _ReturnsSingleSignal(), bars, {}, flat_position(), {}, feature_version=FEATURE_VERSION
        )


def test_empty_list_means_no_signal() -> None:
    """空列表是合法返回值，不是错误。"""
    bars = make_bars([100.0] * 20)
    assert run_decide(_SpyStrategy(), bars, {}, flat_position(), {}, feature_version=FEATURE_VERSION) == []


# --------------------------------------------------------------------------
# 契约 3：输出登记字段校验
# --------------------------------------------------------------------------


class _WrongBarTime:
    lookback = 5
    params_schema: Mapping[str, Any] = {"type": "object", "properties": {}}

    def decide(self, window, bar, params, position, state):  # type: ignore[no-untyped-def]
        return [
            Signal(
                bar_open_time=window.index[-1].to_pydatetime(),  # 窗口里的时间，不是触发 bar
                direction=Direction.LONG,
                trigger_type="x",
                feature_version=FEATURE_VERSION,
            )
        ]


class _WrongFeatureVersion:
    lookback = 5
    params_schema: Mapping[str, Any] = {"type": "object", "properties": {}}

    def decide(self, window, bar, params, position, state):  # type: ignore[no-untyped-def]
        return [
            Signal(
                bar_open_time=bar.open_time,
                direction=Direction.LONG,
                trigger_type="x",
                feature_version="feat-v0-stale",
            )
        ]


class _Shorts:
    lookback = 5
    params_schema: Mapping[str, Any] = {"type": "object", "properties": {}}

    def decide(self, window, bar, params, position, state):  # type: ignore[no-untyped-def]
        return [
            Signal(
                bar_open_time=bar.open_time,
                direction=Direction.SHORT,
                trigger_type="x",
                feature_version=FEATURE_VERSION,
            )
        ]


def test_signal_must_carry_trigger_bar_time() -> None:
    bars = make_bars([100.0] * 20)
    with pytest.raises(ContractViolation, match="bar_open_time"):
        run_decide(_WrongBarTime(), bars, {}, flat_position(), {}, feature_version=FEATURE_VERSION)


def test_signal_must_carry_running_feature_version() -> None:
    bars = make_bars([100.0] * 20)
    with pytest.raises(ContractViolation, match="feature_version"):
        run_decide(
            _WrongFeatureVersion(), bars, {}, flat_position(), {}, feature_version=FEATURE_VERSION
        )


def test_spot_rejects_short() -> None:
    """现货不能做空。运行时明确拒绝，而不是等网关炸掉。"""
    bars = make_bars([100.0] * 20)
    with pytest.raises(ContractViolation, match="允许集合"):
        run_decide(_Shorts(), bars, {}, flat_position(), {}, feature_version=FEATURE_VERSION)


def test_signal_requires_tz_aware_time() -> None:
    from datetime import datetime

    with pytest.raises(ContractViolation, match="时区"):
        Signal(
            bar_open_time=datetime(2024, 1, 1),  # naive
            direction=Direction.LONG,
            trigger_type="x",
            feature_version=FEATURE_VERSION,
        )


def test_signal_requires_trigger_type_and_version() -> None:
    from datetime import datetime, timezone

    t = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ContractViolation):
        Signal(bar_open_time=t, direction=Direction.LONG, trigger_type="", feature_version="v")
    with pytest.raises(ContractViolation):
        Signal(bar_open_time=t, direction=Direction.LONG, trigger_type="x", feature_version="")


def test_bars_index_must_be_tz_aware() -> None:
    naive = make_bars([100.0] * 20).tz_localize(None)
    with pytest.raises(ContractViolation, match="时区"):
        run_decide(_SpyStrategy(), naive, {}, flat_position(), {}, feature_version=FEATURE_VERSION)


# --------------------------------------------------------------------------
# 契约 4：成本模型独立、可校验（修订 ④）
# --------------------------------------------------------------------------


def test_cost_model_valid() -> None:
    cm = CostModel(
        cost_model_id="binance-spot-taker",
        fee_rate=0.001,
        slippage_bps=5.0,
        min_notional=10.0,
    )
    assert cm.funding_included is False  # 现货恒为 false


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fee_rate": -0.001},
        {"slippage_bps": -1.0},
        {"min_notional": -1.0},
    ],
)
def test_cost_model_rejects_negative(kwargs: Mapping[str, float]) -> None:
    base: dict[str, Any] = {
        "cost_model_id": "x",
        "fee_rate": 0.001,
        "slippage_bps": 5.0,
        "min_notional": 10.0,
    }
    base.update(kwargs)
    with pytest.raises(ContractViolation):
        CostModel(**base)


# --------------------------------------------------------------------------
# 契约 5：state 可持久化、可回放（修订 ⑤）
# --------------------------------------------------------------------------


def simulate(
    strategy: Any,
    bars: pd.DataFrame,
    params: Mapping[str, Any],
    *,
    start: int,
    state: dict[str, Any] | None = None,
    position: PositionView | None = None,
) -> list[tuple[int, list[Signal], dict[str, Any], PositionView]]:
    """从 ``start`` 开始逐根推进，返回每根 bar 的 (序号, 信号, 状态快照, 持仓)。

    这就是运行时该做的事的最小版本：状态在各 bar 之间累积，每步都可快照。
    """
    state = state if state is not None else {}
    pos = position if position is not None else flat_position()
    out: list[tuple[int, list[Signal], dict[str, Any], PositionView]] = []

    for i in range(start, len(bars)):
        prefix = bars.iloc[: i + 1]
        sigs = run_decide(strategy, prefix, params, pos, state, feature_version=FEATURE_VERSION)
        if sigs:
            pos = PositionView(
                instrument_id=INSTRUMENT,
                direction=sigs[-1].direction,
                qty=1.0 if sigs[-1].direction is Direction.LONG else 0.0,
                entry_price=float(bars["close"].iloc[i]),
            )
        out.append((i, sigs, copy.deepcopy(state), pos))

    return out


def test_state_is_mutated_and_replayable() -> None:
    """从任意检查点用状态快照重放，后续信号必须逐条相同。

    这条测不出来，就说明策略把状态藏在了 ``state`` 之外（闭包、模块级变量、
    缓存的 DataFrame……），而那种状态在实盘重启后会丢失，回测也复现不了。
    """
    strategy = DonchianBreakout(lookback=21)
    bars = wave_bars(n=400, period=40, amp=8.0)
    params = {"channel": 20}
    start = strategy.lookback + 1

    full = simulate(strategy, bars, params, start=start)
    assert full, "至少要产生一次决策"

    # 至少有一个检查点留下了非空 state，否则这个策略其实是无状态的
    assert any(snap for _, _, snap, _ in full), "DonchianBreakout 应当留下 state"

    for checkpoint in (len(full) // 3, len(full) // 2, len(full) - 2):
        _, _, snap, pos = full[checkpoint]
        if checkpoint + 1 >= len(full):
            continue
        resumed = simulate(
            strategy, bars, params, start=start + checkpoint + 1, state=copy.deepcopy(snap), position=pos
        )
        expected = [(i, sigs) for i, sigs, _, _ in full[checkpoint + 1 :]]
        actual = [(i, sigs) for i, sigs, _, _ in resumed]
        assert actual == expected, f"从检查点 {checkpoint} 重放的信号与原始轨迹不一致"


def test_strategy_does_not_leak_state_outside_dict() -> None:
    """同一个策略实例连续跑两轮，第二轮不能被第一轮的状态污染。"""
    strategy = DonchianBreakout(lookback=21)
    bars = wave_bars(n=300)
    params = {"channel": 20}
    start = strategy.lookback + 1

    first = simulate(strategy, bars, params, start=start)
    second = simulate(strategy, bars, params, start=start)

    assert [(i, s) for i, s, _, _ in first] == [(i, s) for i, s, _, _ in second]


def test_determinism() -> None:
    """相同输入必须给出相同输出。任何随机性或时钟依赖都会破坏可复现性。"""
    for strategy, params in ((SmaCross(), {"fast": 20, "slow": 60}), (DonchianBreakout(), {"channel": 20})):
        bars = wave_bars(n=250)
        a = simulate(strategy, bars, params, start=strategy.lookback + 1)
        b = simulate(strategy, bars, params, start=strategy.lookback + 1)
        assert [(i, s) for i, s, _, _ in a] == [(i, s) for i, s, _, _ in b]


def test_decide_does_not_mutate_inputs() -> None:
    """策略不得修改传入的窗口与 bar。"""
    strategy = SmaCross()
    bars = trend_bars(n=200)
    prefix = bars.iloc[: 150]
    before = prefix.copy(deep=True)
    bar_before = make_bar_view(prefix)

    run_decide(strategy, prefix, {"fast": 20, "slow": 60}, flat_position(), {}, feature_version=FEATURE_VERSION)

    pd.testing.assert_frame_equal(prefix, before)
    assert make_bar_view(prefix) == bar_before


# --------------------------------------------------------------------------
# 契约 6：特征无未来依赖
# --------------------------------------------------------------------------


@given(st.lists(st.floats(min_value=1.0, max_value=10_000.0, allow_nan=False, allow_infinity=False), min_size=80, max_size=120))
@settings(max_examples=25, deadline=None)
def test_features_have_no_lookahead(closes: list[float]) -> None:
    """``compute(bars[:k])`` 必须等于 ``compute(bars)[:k]``。

    这是未来数据最严格的定义：t 时刻的特征只能用 t 及之前的数据。
    任何 ``shift(-1)``、``center=True``、``bfill()`` 都会让这条断言失败。
    """
    bars = make_bars(closes)
    full = compute(bars)
    for k in (14, 21, 40, 61, len(bars)):
        if k > len(bars):
            continue
        partial = compute(bars.iloc[:k])
        pd.testing.assert_frame_equal(partial, full.iloc[:k], check_freq=False)


def test_features_reject_unknown_version() -> None:
    from keel.features import UnknownFeatureVersion

    with pytest.raises(UnknownFeatureVersion):
        compute(make_bars([100.0] * 100), "feat-v999")


def test_donchian_is_shifted_below_current_bar() -> None:
    """上轨不能包含触发 bar 自己的最高价，否则「突破」永远成立。"""
    bars = make_bars([100.0] * 30 + [200.0])
    upper, lower = donchian(bars["high"], bars["low"], 20)

    # 触发 bar 自己 high=202，但上轨只能是它之前 20 根的最高（101）
    assert upper.iloc[-1] == pytest.approx(101.0)


def test_features_keep_nan_instead_of_zero() -> None:
    """窗口不足处必须是 NaN。填 0 会让策略在冷启动时产生虚假信号。"""
    feats = compute(make_bars([100.0] * 10))
    assert feats["sma_20"].isna().all()
    assert not (feats["sma_20"] == 0).any()


# --------------------------------------------------------------------------
# 六项契约项的结构性检查（A9 的另外几项）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_cls", [SmaCross, DonchianBreakout])
def test_implements_protocol(strategy_cls: type) -> None:
    """两个玩具策略都必须满足 ``Strategy`` Protocol。"""
    instance = strategy_cls()
    assert isinstance(instance, Strategy)
    assert isinstance(instance.lookback, int) and instance.lookback >= 1
    assert isinstance(instance.params_schema, Mapping)
    assert instance.params_schema.get("type") == "object"


@pytest.mark.parametrize("strategy_cls", [SmaCross, DonchianBreakout])
def test_strategy_params_schema_is_well_formed(strategy_cls: type) -> None:
    """params_schema 至少要是能被当成 JSON Schema 用的结构。"""
    schema = strategy_cls().params_schema
    assert "properties" in schema
    for name, spec in schema["properties"].items():
        assert isinstance(name, str) and name
        assert "type" in spec
        if spec["type"] == "integer":
            assert spec["minimum"] <= spec.get("default", spec["minimum"]) <= spec["maximum"]


def test_strategies_do_not_compute_features_themselves() -> None:
    """策略模块里不许出现裸 ``.rolling(``、``.ewm(``、时钟读取、I/O。

    这是「特征入口」契约项的 CI 化：指标只能走 ``keel.features``。
    静态检查比代码评审可靠 —— 评审会漏，CI 不会。
    """
    src = Path(__file__).resolve().parent.parent / "keel" / "strategies.py"
    text = src.read_text(encoding="utf-8")

    forbidden = [
        r"\.rolling\(",
        r"\.ewm\(",
        r"\.expanding\(",
        r"\bdatetime\.now\(",
        r"\btime\.time\(",
        r"\bopen\(",
        r"\bsubprocess\b",
        r"\brequests\b",
        r"\bimport\s+random\b",
    ]
    for pattern in forbidden:
        hits = re.findall(pattern, text)
        assert not hits, f"keel/strategies.py 出现了禁止的 {pattern!r}（{len(hits)} 处）"


def test_strategies_only_import_from_keel_features_for_indicators() -> None:
    src = Path(__file__).resolve().parent.parent / "keel" / "strategies.py"
    text = src.read_text(encoding="utf-8")
    assert "from keel.features import" in text, "策略应当从 keel.features 取指标原语"


# --------------------------------------------------------------------------
# 端到端：两个玩具策略真的能出信号
# --------------------------------------------------------------------------


def test_sma_cross_produces_signals_on_trend() -> None:
    strategy = SmaCross(lookback=61)
    # 先横盘 120 根让快慢均线收敛，再单边上涨 —— 金叉会落在扫描区间内。
    # 若一开始就上涨，金叉在 start 之前就发生了，测试会假失败。
    bars = make_bars([100.0] * 120 + [100.0 + i * 2 for i in range(200)])
    records = simulate(strategy, bars, {"fast": 20, "slow": 60}, start=strategy.lookback + 1)

    signals = [s for _, sigs, _, _ in records for s in sigs]
    assert signals, "横盘后单边上涨应当出现金叉"
    assert signals[0].direction is Direction.LONG
    assert signals[0].trigger_type == "sma_cross_up"


def test_donchian_breakout_produces_signals_on_waves() -> None:
    strategy = DonchianBreakout(lookback=21)
    bars = wave_bars(n=400, period=40, amp=8.0)
    records = simulate(strategy, bars, {"channel": 20}, start=strategy.lookback + 1)

    triggers = {s.trigger_type for _, sigs, _, _ in records for s in sigs}
    assert "donchian_breakout_up" in triggers
    assert "donchian_breakout_down" in triggers


def test_all_signals_carry_feature_version() -> None:
    strategy = DonchianBreakout(lookback=21)
    bars = wave_bars(n=300)
    records = simulate(strategy, bars, {"channel": 20}, start=strategy.lookback + 1)
    for _, sigs, _, _ in records:
        for s in sigs:
            assert s.feature_version == FEATURE_VERSION


def test_no_duplicate_long_signals_on_flat_market() -> None:
    """横盘时不应反复出同向信号 —— 这是 state 存在的意义。"""
    strategy = DonchianBreakout(lookback=21)
    bars = make_bars([100.0 + (i % 3) * 0.01 for i in range(300)])
    records = simulate(strategy, bars, {"channel": 20}, start=strategy.lookback + 1)
    longs = [s for _, sigs, _, _ in records for s in sigs if s.direction is Direction.LONG]
    assert len(longs) <= 1


# --------------------------------------------------------------------------
# 契约 7：参数不得超出声明窗口（修订 ⑥，本文件跑出来的真缺陷）
# --------------------------------------------------------------------------


def test_param_beyond_lookback_raises_instead_of_silently_producing_nothing() -> None:
    """``slow=90`` 配 ``lookback=61``：必须报错，不能默默不出信号。

    这是本文件在真实运行中发现的缺陷。schema 允许 slow 到 120，
    但 lookback=61 只能给 60 根历史，结果是一根信号都不出、日志里什么都没有。
    「静默无输出」比「报错」危险得多 —— 报错会被人看见，沉默不会。
    """
    bars = make_bars([100.0] * 120 + [100.0 + i * 2 for i in range(300)])
    strat = SmaCross(lookback=61)

    with pytest.raises(ParamExceedsWindow, match="lookback"):
        run_decide(
            strat,
            bars.iloc[:200],
            {"fast": 20, "slow": 90},
            flat_position(),
            {},
            feature_version=FEATURE_VERSION,
        )


def test_param_beyond_lookback_is_caught_before_trading() -> None:
    """配置错误应当在提交阶段暴露，而不是等到实盘跑了几周才觉得策略不行。"""
    for slow in (61, 80, 120):
        assert SmaCross().required_lookback({"fast": 20, "slow": slow}) == slow

    with pytest.raises(ParamExceedsWindow):
        check_window_fits(SmaCross(lookback=61), {"fast": 20, "slow": 62})
    check_window_fits(SmaCross(lookback=61), {"fast": 20, "slow": 60})


def test_donchian_required_lookback_accounts_for_shift() -> None:
    """``channel=20`` 实际需要 21 根：donchian 内部 shift(1) 吃掉一根。"""
    assert DonchianBreakout().required_lookback({"channel": 20}) == 21
    with pytest.raises(ParamExceedsWindow):
        check_window_fits(DonchianBreakout(lookback=21), {"channel": 21})


def test_enlarged_lookback_makes_long_params_work() -> None:
    """调大 lookback 后，长参数应当正常出信号 —— 证明约束是可以满足的。"""
    bars = make_bars([100.0] * 150 + [100.0 + i * 2 for i in range(300)])
    wide = SmaCross(lookback=100)
    short = SmaCross(lookback=61)

    wide_sigs = [
        s
        for _, sigs, _, _ in simulate(wide, bars, {"fast": 20, "slow": 90}, start=101)
        for s in sigs
    ]
    assert wide_sigs, "lookback=100 时 slow=90 应当能出信号"

    with pytest.raises(ParamExceedsWindow):
        simulate(short, bars, {"fast": 20, "slow": 90}, start=62)


def test_check_window_fits_falls_back_for_strategies_without_required_lookback() -> None:
    """老策略（只声明 lookback、不实现 required_lookback）仍能跑，行为等同旧契约。"""

    class Legacy:
        lookback = 10
        params_schema: Mapping[str, Any] = {"type": "object", "properties": {}}

        def decide(self, window, bar, params, position, state):  # type: ignore[no-untyped-def]
            return []

    assert check_window_fits(Legacy(), {}) == 10
