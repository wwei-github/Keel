"""把一条回测作业跑完。

职责边界：这里**不写数据库**。``execute_run`` 是纯计算，``run_once`` 负责
认领/落账。拆开的原因很简单：失败要能被测试，而测试不该连着一个真库。
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from keel.contracts import CostModel
from keel.data.lake import read_bars
from keel.research.metrics import compute_metrics
from keel.research.registry import get as get_strategy
from keel.research.simulator import simulate

DEFAULT_COST = CostModel(
    cost_model_id="default",
    fee_rate=0.001,
    slippage_bps=5.0,
    min_notional=5.0,
    funding_included=False,
)


def _cost_model(cost_model_id: str) -> CostModel:
    """V1 只有内置默认模型；从 PG 读表是 V1.5 的事（cost_models 已建好表）。"""
    return DEFAULT_COST


def execute_run(run, *, raw_root: Path | None = None) -> dict:
    from keel.config import get_settings

    root = Path(raw_root or get_settings().raw_root)
    strategy = get_strategy(run.strategy_key, run.strategy_version)
    cost = _cost_model(run.cost_model_id)
    per_symbol = []
    for symbol in run.universe:
        bars = read_bars(root, symbol=symbol, interval=run.interval)
        bars = bars[(bars["open_time"] >= run.start_time) & (bars["open_time"] <= run.end_time)]
        if bars.empty:
            continue
        sim = simulate(bars, strategy, run.params, cost)
        per_symbol.append((sim, compute_metrics(sim.equity, sim.trades, run.interval)))
    if not per_symbol:
        raise ValueError(f"no bars for universe {run.universe}")
    # 等权组合：各标的收益率取平均
    equity = sum(s.equity / s.equity.iloc[0] for s, _ in per_symbol) / len(per_symbol)
    equity = equity * per_symbol[0][0].equity.iloc[0]
    trades = tuple(t for s, _ in per_symbol for t in s.trades)
    return {
        "metrics": compute_metrics(equity, trades, run.interval),
        "per_symbol": {s.equity.name or str(i): m for i, (s, m) in enumerate(per_symbol)},
        "bars": sum(len(s.equity) for s, _ in per_symbol),
        "equity": {str(k): float(v) for k, v in equity.items()},
        # Trade 是 frozen slots dataclass，没有 __dict__；用 asdict。
        "trades": [asdict(t) for t in trades],
    }


def run_once(*, raw_root: Path | None = None) -> bool:
    """认领一条并跑完。没有任务返回 False。"""
    from keel.research import backtest

    backtest.sweep_orphans()
    run = backtest.claim_next()
    if run is None:
        return False
    try:
        result = execute_run(run, raw_root=raw_root)
        # 报告先落制品再标记成功：反过来的话，SUCCEEDED 但没有报告，
        # 前端点开就是空页，而且没人知道是漏写还是没算出来。
        persist_report(run, result)
        backtest.finish(run.run_id, metrics=result["metrics"])
    except Exception as exc:
        backtest.finish(run.run_id, error=f"{type(exc).__name__}: {exc}")
        raise
    return True


def persist_report(run, result: dict, *, root=None) -> str:
    """把一次回测的报告写成制品。返回 artifact_id。

    报告里必须带 ``disclaimer``（A8）——一个没有标注「模拟回测」的收益率曲线
    截图流出去，就是一次误导。所以这句话由代码写，不靠人记得加。
    """
    from keel.reports import artifacts

    payload = {
        "run_id": run.run_id,
        "strategy_key": run.strategy_key,
        "strategy_version": run.strategy_version,
        "interval": run.interval,
        "universe": run.universe,
        "start": str(run.start_time),
        "end": str(run.end_time),
        "engine": run.engine,
        "metrics": result["metrics"],
        "disclaimer": "模拟回测，非实盘业绩",
    }
    return artifacts.write_artifact("report", payload, run_id=run.run_id, root=root)
