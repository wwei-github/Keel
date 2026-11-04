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


def test_default_cost_model_exists_after_migrate(pg_dsn: str) -> None:
    """默认成本模型由迁移种下，不依赖人工插入。

    没有这一行时，提交回测会在「跑起来」那一刻才失败——现场表现是
    队列里一堆 PENDING 却不动的任务，而 API 返回 201。
    """
    migrations.migrate(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        row = conn.execute(
            "select fee_rate, slippage_bps, min_notional, funding_included"
            " from cost_models where cost_model_id = 'default'"
        ).fetchone()
    assert row is not None, "迁移没有种下 default 成本模型"
    fee_rate, slippage_bps, min_notional, funding_included = row
    assert float(fee_rate) == 0.001
    assert float(slippage_bps) == 5.0
    assert float(min_notional) == 5.0
    assert funding_included is False


def test_seeded_cost_model_matches_runner_default(pg_dsn: str) -> None:
    """表里的默认值与 runner 里的 DEFAULT_COST 必须一致。

    不一致时，同一次回测会因「参数从 API 传入还是走默认」而得到不同
    指标——这种差异极难在结果里看出来，只会表现为「数字对不上」。
    """
    from keel.research.runner import DEFAULT_COST

    migrations.migrate(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        row = conn.execute(
            "select fee_rate, slippage_bps, min_notional, funding_included"
            " from cost_models where cost_model_id = %s",
            (DEFAULT_COST.cost_model_id,),
        ).fetchone()
    assert row is not None
    assert float(row[0]) == float(DEFAULT_COST.fee_rate)
    assert float(row[1]) == float(DEFAULT_COST.slippage_bps)
    assert float(row[2]) == float(DEFAULT_COST.min_notional)
    assert row[3] is DEFAULT_COST.funding_included
