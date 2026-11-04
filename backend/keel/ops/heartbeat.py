"""心跳。写库不写日志——容器挂了日志也就没了，而"容器挂了"正是要发现的场景。"""

from __future__ import annotations


def beat(component: str) -> None:
    from keel.db.pool import transaction

    with transaction() as conn:
        conn.execute(
            """insert into heartbeats (component, beat_at) values (%s, now())
               on conflict (component) do update set beat_at = now()""",
            (component,),
        )


def check(*, interval_seconds: int = 60, threshold: int = 3) -> list[str]:
    """返回失联组件清单。阈值按周期数算，不按绝对秒数——改心跳频率不必改告警。"""
    from keel.db.pool import connection

    with connection() as conn:
        rows = conn.execute(
            """select component from heartbeats
                where beat_at < now() - make_interval(secs => %s)
                order by component""",
            (interval_seconds * threshold,),
        ).fetchall()
    return [r["component"] for r in rows]
