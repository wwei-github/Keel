"""回测作业队列：入队幂等、认领排他、孤儿回收。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from keel.research import backtest


@pytest.fixture(autouse=True)
def _clean(pg_dsn: str) -> None:
    import psycopg

    from keel.db import migrations

    migrations.migrate(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("delete from backtest_runs")
        conn.commit()
    import os

    os.environ["KEEL_PG_DSN"] = pg_dsn
    from keel.config import get_settings
    from keel.db import pool

    get_settings.cache_clear()
    pool.reset_pool()


def _enqueue(**kw):
    base = {
        "universe": ["BTC/USDT"],
        "interval": "1h",
        "start": datetime(2023, 1, 1, tzinfo=UTC),
        "end": datetime(2023, 6, 1, tzinfo=UTC),
        "strategy_key": "sma_cross",
        "strategy_version": 1,
        "params": {"fast": 20, "slow": 60},
        "cost_model_id": "default",
    }
    base.update(kw)
    return backtest.enqueue(**base)


def test_enqueue_returns_queued_run(pg_dsn: str) -> None:
    run_id = _enqueue()
    run = backtest.get(run_id)
    assert run.status is backtest.RunStatus.QUEUED
    assert run.strategy_key == "sma_cross"


def test_idempotency_key_returns_same_run(pg_dsn: str) -> None:
    first = _enqueue(idempotency_key="abc")
    second = _enqueue(idempotency_key="abc")
    assert first == second


def test_claim_is_exclusive(pg_dsn: str) -> None:
    run_id = _enqueue()
    claimed = backtest.claim_next()
    assert claimed is not None and claimed.run_id == run_id
    assert claimed.status is backtest.RunStatus.RUNNING
    assert backtest.claim_next() is None


def test_finish_marks_succeeded(pg_dsn: str) -> None:
    run_id = _enqueue()
    backtest.claim_next()
    backtest.finish(run_id, metrics={"sharpe": 1.2})
    run = backtest.get(run_id)
    assert run.status is backtest.RunStatus.SUCCEEDED
    assert run.metrics == {"sharpe": 1.2}


def test_sweep_orphans_reclaims_stale_running(pg_dsn: str) -> None:
    import psycopg

    run_id = _enqueue()
    backtest.claim_next()
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "update backtest_runs set started_at = now() - interval '10 hours' where run_id = %s",
            (run_id,),
        )
        conn.commit()
    assert backtest.sweep_orphans(timeout_seconds=60) == 1
    assert backtest.get(run_id).status is backtest.RunStatus.FAILED
    assert backtest.get(run_id).fail_reason is backtest.FailReason.ORPHANED
