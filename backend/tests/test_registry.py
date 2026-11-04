"""策略注册表：按 key 取实例，未知 key 报明确错误。"""

from __future__ import annotations

import pytest

from keel.research import registry


def test_known_strategies_are_registered() -> None:
    # keys() 返回 list[tuple]，不是 dict，所以这里不能省掉 .keys()
    assert ("sma_cross", 1) in registry.keys()  # noqa: SIM118
    assert ("donchian_breakout", 1) in registry.keys()  # noqa: SIM118


def test_get_returns_usable_instance() -> None:
    strat = registry.get("sma_cross")
    assert strat.lookback == 61
    assert isinstance(strat.params_schema, dict)


def test_unknown_key_raises_with_hint() -> None:
    with pytest.raises(registry.UnknownStrategy, match="sma_cross"):
        registry.get("nope")


def test_version_mismatch_raises() -> None:
    with pytest.raises(registry.UnknownStrategy, match="version"):
        registry.get("sma_cross", version=99)
