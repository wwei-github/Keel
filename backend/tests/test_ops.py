"""心跳与告警抑制。"""

from __future__ import annotations

import pytest

from keel.ops import alerts, heartbeat


@pytest.fixture(autouse=True)
def _dsn(pg_dsn: str, monkeypatch) -> None:
    from keel.db import migrations

    migrations.migrate(pg_dsn)
    monkeypatch.setenv("KEEL_PG_DSN", pg_dsn)
    from keel.config import get_settings
    from keel.db import pool

    get_settings.cache_clear()
    pool.reset_pool()
    import psycopg

    with psycopg.connect(pg_dsn) as conn:
        conn.execute("delete from heartbeats")
        conn.commit()


def test_beat_then_check_is_clean() -> None:
    heartbeat.beat("api")
    assert heartbeat.check() == []


def test_missing_component_is_reported() -> None:
    import psycopg

    heartbeat.beat("api")
    with psycopg.connect(__import__("os").environ["KEEL_PG_DSN"]) as conn:
        conn.execute(
            "insert into heartbeats (component, beat_at) values "
            "('etl-worker', now() - interval '10 minutes')"
        )
        conn.commit()
    assert heartbeat.check(interval_seconds=60, threshold=3) == ["etl-worker"]


def test_alert_suppressed_within_window(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(alerts, "_deliver", lambda *a, **k: sent.append(a[0]))
    alerts.send(alerts.Severity.P1, "test-key", "t", "b")
    alerts.send(alerts.Severity.P1, "test-key", "t", "b")
    assert len(sent) == 1


def test_different_keys_are_not_suppressed(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(alerts, "_deliver", lambda *a, **k: sent.append(a[0]))
    alerts.send(alerts.Severity.P0, "k1", "t", "b")
    alerts.send(alerts.Severity.P0, "k2", "t", "b")
    assert len(sent) == 2


def test_jobs_cover_documented_schedule() -> None:
    from keel.scheduler.jobs import JOBS

    ids = {j.job_id for j in JOBS}
    assert {
        "daily-backfill",
        "build-marts",
        "coverage-check",
        "heartbeat-check",
        "archive-and-compress",
    } <= ids
    assert all("TZ" not in j.cron for j in JOBS)  # cron 表达式本身不带时区，注册时统一 UTC
