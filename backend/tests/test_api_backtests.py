"""API-03/04/05：入队、幂等、契约字段。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from keel.api.app import create_app
from keel.db import migrations

BODY = {
    "strategy_key": "sma_cross",
    "strategy_version": 1,
    "universe": ["BTC/USDT"],
    "interval": "1h",
    "from": "2023-01-01T00:00:00Z",
    "to": "2023-06-01T00:00:00Z",
    "params": {"fast": 20, "slow": 60},
    "cost_model_id": "default",
    "engine": "keel-bar",
}


@pytest.fixture()
def client(pg_dsn: str, monkeypatch) -> TestClient:
    import psycopg

    migrations.migrate(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("delete from backtest_runs")
        conn.commit()
    monkeypatch.setenv("KEEL_PG_DSN", pg_dsn)
    from keel.config import get_settings
    from keel.db import pool

    get_settings.cache_clear()
    pool.reset_pool()
    return TestClient(create_app())


def test_submit_returns_queued(client: TestClient) -> None:
    r = client.post("/api/v1/backtests", json=BODY)
    assert r.status_code == 201
    assert r.json()["data"]["status"] == "QUEUED"
    assert r.json()["data"]["run_id"]


def test_submit_requires_cost_model(client: TestClient) -> None:
    body = dict(BODY)
    body.pop("cost_model_id")
    assert client.post("/api/v1/backtests", json=body).status_code == 422


def test_submit_requires_universe(client: TestClient) -> None:
    body = dict(BODY, universe=[])
    assert client.post("/api/v1/backtests", json=body).status_code == 422


def test_idempotency_key_reuses_run(client: TestClient) -> None:
    first = client.post("/api/v1/backtests", json=BODY, headers={"Idempotency-Key": "k1"})
    second = client.post("/api/v1/backtests", json=BODY, headers={"Idempotency-Key": "k1"})
    assert first.json()["data"]["run_id"] == second.json()["data"]["run_id"]


def test_get_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/backtests/nope").status_code == 404


def test_list_returns_created_run(client: TestClient) -> None:
    client.post("/api/v1/backtests", json=BODY)
    r = client.get("/api/v1/backtests", params={"limit": 10})
    assert len(r.json()["data"]) == 1
