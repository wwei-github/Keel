"""PG 模式与连接层。"""

from __future__ import annotations

import psycopg

from keel.db import migrations, pool


def test_migrate_is_idempotent_and_creates_v1_tables(pg_dsn: str) -> None:
    migrations.migrate(pg_dsn)
    migrations.migrate(pg_dsn)  # 第二次必须也成功，迁移要可重入
    with psycopg.connect(pg_dsn) as conn:
        names = {
            r[0]
            for r in conn.execute("select tablename from pg_tables where schemaname = 'public'")
        }
    expected = {
        "instruments",
        "cost_models",
        "strategy_configs",
        "sleeves",
        "strategy_instances",
        "runtime_flags",
        "audit_log",
        "events",
        "consumer_offsets",
        "consumed_events",
        "backtest_runs",
        "job_runs",
        "artifacts",
        "heartbeats",
        "late_events",
    }
    assert expected <= names


def test_backtest_runs_has_universe_and_metrics(pg_dsn: str) -> None:
    migrations.migrate(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        cols = {
            r[0]
            for r in conn.execute(
                "select column_name from information_schema.columns "
                "where table_name = 'backtest_runs'"
            )
        }
    assert {"universe", "engine", "engine_version", "metrics", "data_snapshot_id"} <= cols


def test_connection_yields_dict_rows(pg_dsn: str, monkeypatch) -> None:
    monkeypatch.setenv("KEEL_PG_DSN", pg_dsn)
    from keel.config import get_settings

    get_settings.cache_clear()
    pool.reset_pool()
    with pool.connection() as conn:
        assert conn.execute("select 1 as one").fetchone()["one"] == 1
    pool.reset_pool()
