"""执行网关（V2）。§3.5。

下单链路：信号 → 风控闸门（进程内同步）→ 交易所 → 回报 → 落库。

关于``ORDER_UNKNOWN_STATE``：网络超时后交易所到底收没收到订单，本地无法确定。
此时必须查单而不是重试——重试可能开出两倍仓位。这是系统里唯一
"宁可停下等人"的分支，实现时不要图省事改成自动重试。
"""

from __future__ import annotations


def submit(order_request: dict) -> dict:
    """校验 → 风控 → 提交。返回带 ``client_order_id`` 的结果。"""
    raise NotImplementedError("执行网关尚未实现：V2 才需要实盘")
