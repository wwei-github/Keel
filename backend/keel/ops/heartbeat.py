"""心跳。§2.5.7。

每个常驻组件每分钟写一条。检查器每 5 分钟跑一次，超过
``heartbeat_miss_threshold``（默认 3）个周期没有心跳即告警。

心跳要写库而不是只打日志：日志在容器里，容器挂了日志也就没了——
而"容器挂了"正是需要被发现的场景。
"""

from __future__ import annotations


def beat(component: str) -> None:
    raise NotImplementedError("心跳写入尚未实现：等待 PG 连接层")


def check() -> list[str]:
    """返回失联组件清单。"""
    raise NotImplementedError("心跳检查尚未实现")
