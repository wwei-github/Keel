"""事件通道（``events`` 表 + ``LISTEN/NOTIFY``）。§2.5.2、§6.4。

投递语义（§2.5.3）：至少一次、按 ``partition_key`` 有序、30 天可重放。
幂等由消费方负责——事件总线只保证送达，不保证只达一次；
``consumed_events(consumer, event_id)`` 是消费方去重的那张表。

``seq bigserial`` 才是消费水位。不能用 ``event_id``（uuid）当水位：
uuid 无序，``WHERE event_id > last_event_id`` 等于随机筛选。
"""

from __future__ import annotations

TOPICS: tuple[str, ...] = (
    "bar.closed.v1",
    "signal.created.v1",
    "order.submitted.v1",
    "order.filled.v1",
    "position.changed.v1",
    "control.changed.v1",
    "backtest.queued.v1",
    "backtest.finished.v1",
    "mart.published.v1",
)


def publish(topic: str, partition_key: str, payload: dict) -> int:
    """写入 ``events`` 并 NOTIFY，返回 ``seq``。"""
    raise NotImplementedError("事件发布尚未实现：等待 PG 连接层")


def subscribe(topic: str, consumer: str):
    """从 ``consumer_offsets`` 续上，逐条 yield。"""
    raise NotImplementedError("事件订阅尚未实现")
