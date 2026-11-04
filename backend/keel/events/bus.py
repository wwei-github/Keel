"""事件通道（``events`` 表 + ``LISTEN/NOTIFY``）。§2.5.2、§6.4。

投递语义（§2.5.3）：至少一次、按 ``partition_key`` 有序、30 天可重放。
幂等由消费方负责——事件总线只保证送达，不保证只达一次；
``consumed_events(consumer, event_id)`` 是消费方去重的那张表。

``seq bigserial`` 才是消费水位。不能用 ``event_id``（uuid）当水位：
uuid 无序，``WHERE event_id > last_event_id`` 等于随机筛选。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from psycopg import sql

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

CHANNEL = "keel_events"
REPLAY_DAYS = 30


def publish(topic: str, partition_key: str, payload: dict) -> int:
    """写入 ``events`` 并 NOTIFY，返回 ``seq``。

    事件与业务写入同库同事务时，调用方应在自己的 ``transaction()`` 里先写业务表
    再调这里——这样「业务状态改了但事件没发」不可能发生。
    """
    from keel.db.pool import transaction

    if topic not in TOPICS:
        raise ValueError(f"unknown topic: {topic}")
    with transaction() as conn:
        row = conn.execute(
            """insert into events (event_id, topic, partition_key, payload)
               values (gen_random_uuid(), %s, %s, %s::jsonb)
               returning seq""",
            (topic, partition_key, json.dumps(payload)),
        ).fetchone()
        # NOTIFY 不接受绑定参数（它是语句级的，不是表达式），必须内联字面量。
        # 用 psycopg 的 sql.Literal 转义，别自己拼引号。
        conn.execute(
            sql.SQL("notify {}, {}").format(
                sql.Identifier(CHANNEL),
                sql.Literal(json.dumps({"seq": row["seq"], "topic": topic})),
            )
        )
    return int(row["seq"])


def subscribe(topic: str, consumer: str, *, batch: int = 100) -> Iterator[dict]:
    """从 ``consumer_offsets`` 续上，逐条 yield。

    每 yield 一条就推进水位——「连着处理 100 条再一起提交水位」看似更快，
    但中途崩溃会重放 100 条，而消费方去重表只在有事件 id 时才有用。
    """
    from keel.db.pool import connection, transaction

    if topic not in TOPICS:
        raise ValueError(f"unknown topic: {topic}")

    while True:
        with connection() as conn:
            offset = conn.execute(
                "select last_seq from consumer_offsets where consumer = %s and topic = %s",
                (consumer, topic),
            ).fetchone()
            last = int(offset["last_seq"]) if offset else 0
            rows = conn.execute(
                """select seq, event_id, topic, partition_key, payload, created_at
                     from events
                    where topic = %s and seq > %s
                    order by seq
                    limit %s""",
                (topic, last, batch),
            ).fetchall()
        if not rows:
            return
        for row in rows:
            yield dict(row)
            with transaction() as conn:
                conn.execute(
                    """insert into consumer_offsets (consumer, topic, last_seq)
                       values (%s, %s, %s)
                       on conflict (consumer, topic)
                       do update set last_seq = excluded.last_seq""",
                    (consumer, topic, row["seq"]),
                )
                conn.execute(
                    """insert into consumed_events (consumer, event_id)
                       values (%s, %s) on conflict do nothing""",
                    (consumer, row["event_id"]),
                )


def already_consumed(consumer: str, event_id) -> bool:
    from keel.db.pool import connection

    with connection() as conn:
        row = conn.execute(
            "select 1 from consumed_events where consumer = %s and event_id = %s",
            (consumer, event_id),
        ).fetchone()
    return row is not None


def purge_consumed(*, older_than_days: int = REPLAY_DAYS) -> int:
    """去重表只留 30 天：再久的事件已经不在重放窗口里，留着只是长表。"""
    from keel.db.pool import transaction

    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    with transaction() as conn:
        rows = conn.execute(
            "delete from events where created_at < %s returning seq", (cutoff,)
        ).fetchall()
    return len(rows)
