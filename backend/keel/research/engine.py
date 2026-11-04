"""回测引擎适配层。

V1 只有一套引擎 ``keel-bar``（ADR-009）：逐 bar 走 ``keel.contracts.run_decide``，
与实盘决策路径是同一份代码。Freqtrade 适配推到 V1.5——它的策略接口是
``IStrategy.populate_*``，与 ``decide()`` 契约不同，适配层是一整块独立工作。

VectorBT 在 V1 也不在验收范围内（ADR-003）：它的成本模型是桶化的，
要求它与逐笔引擎逐值相等等于要求两套模型变成同一套模型。
"""

from __future__ import annotations

from dataclasses import asdict
from enum import StrEnum


class Engine(StrEnum):
    KEEL_BAR = "keel-bar"
    VECTORBT = "vectorbt"
    FREQTRADE = "freqtrade"


def run(engine: Engine, request: dict) -> dict:
    """按引擎跑一次回测。V1 只有 ``keel-bar``。"""
    if Engine(engine) is not Engine.KEEL_BAR:
        raise NotImplementedError(f"engine {engine} 不在 V1 范围（ADR-003/ADR-009）")

    from keel.research.metrics import compute_metrics
    from keel.research.simulator import simulate

    sim = simulate(
        request["bars"],
        request["strategy"],
        request.get("params") or {},
        request["cost"],
        initial_cash=request.get("initial_cash", 10_000.0),
    )
    return {
        "metrics": compute_metrics(sim.equity, sim.trades, request["interval"]),
        "equity": {str(k): float(v) for k, v in sim.equity.items()},
        "trades": [asdict(t) for t in sim.trades],
    }
