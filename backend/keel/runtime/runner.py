"""策略运行时（V2）。§3.4。

三件事：
1. 按 ``strategy.required_lookback(params)`` 预取窗口——用 required 而不是 lookback，
   前者是实际需求、后者是上界（ADR-007）。
2. 逐 bar 调 ``keel.contracts.run_decide``，把返回的信号交给执行网关。
3. 把 ``state`` 持久化到 ``strategy_instances.state``，崩溃重启能续上。

本模块不碰风控、不碰下单——那是 ``keel.execution`` 的事。
"""

from __future__ import annotations


def tick(instance_id: str) -> None:
    """处理一根已收盘的 bar。幂等：同一根 bar 重复调用只产生一次信号。"""
    raise NotImplementedError("运行时尚未实现：V2 才需要实盘")
