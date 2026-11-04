"""事件总线：序号水位、幂等去重、重放。"""

from __future__ import annotations

import pytest

from keel.events import bus


@pytest.fixture(autouse=True)
def _dsn(pg_dsn: str, monkeypatch) -> None:
    import psycopg

    from keel.db import migrations

    migrations.migrate(pg_dsn)
    monkeypatch.setenv("KEEL_PG_DSN", pg_dsn)
    from keel.config import get_settings
    from keel.db import pool

    get_settings.cache_clear()
    pool.reset_pool()
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("delete from events")
        conn.execute("delete from consumer_offsets")
        conn.execute("delete from consumed_events")
        conn.commit()


def test_publish_returns_increasing_seq() -> None:
    first = bus.publish("bar.closed.v1", "BTC/USDT", {"sym": "BTC/USDT"})
    second = bus.publish("bar.closed.v1", "BTC/USDT", {"sym": "BTC/USDT"})
    assert second > first


def test_unknown_topic_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown topic"):
        bus.publish("nope.v1", "k", {})


def test_subscribe_resumes_from_offset() -> None:
    bus.publish("bar.closed.v1", "BTC/USDT", {"i": 1})
    bus.publish("bar.closed.v1", "BTC/USDT", {"i": 2})
    first = list(bus.subscribe("bar.closed.v1", "consumer-a"))
    assert [e["payload"]["i"] for e in first] == [1, 2]
    # 再次订阅时位点已经推进，没有新事件就立刻返回（不是死循环等新事件）
    assert list(bus.subscribe("bar.closed.v1", "consumer-a")) == []


def test_consumed_events_dedupes() -> None:
    bus.publish("bar.closed.v1", "BTC/USDT", {"i": 1})
    events = list(bus.subscribe("bar.closed.v1", "consumer-b"))
    assert bus.already_consumed("consumer-b", events[0]["event_id"]) is True
    # 不同消费方互不影响
    assert bus.already_consumed("consumer-c", events[0]["event_id"]) is False
