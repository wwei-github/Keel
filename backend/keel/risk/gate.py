"""风控闸门。§3.6。

纯函数优先：``check()`` 只吃持仓快照、配置与待下订单，返回 ``Decision``。
不读库、不发请求、不写日志——这样才可能被单测穷举。

它作为 ``execution-gateway`` 进程内模块运行，不单独成容器（§2.5.1）：
同步调用它校验的是内存里的快照，拆出去只会多一次网络往返和一个新故障点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RejectReason(StrEnum):
    KILL_SWITCH = "KILL_SWITCH"
    MAX_POSITION = "MAX_POSITION"
    MAX_EXPOSURE = "MAX_EXPOSURE"
    DAILY_LOSS = "DAILY_LOSS"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    MIN_NOTIONAL = "MIN_NOTIONAL"


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: RejectReason | None = None
    details: dict = field(default_factory=dict)


def check(order: dict, snapshot: dict, limits: dict) -> Decision:
    """按顺序检查：总开关 → 仓位 → 敞口 → 日亏损 → 余额 → 最小下单量。

    顺序是有意的：越廉价的检查越靠前，总开关排第一是因为它一票否决。
    """
    raise NotImplementedError("风控闸门尚未实现：V2 才需要实盘")
