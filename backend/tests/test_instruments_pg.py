"""主数据落库：upsert 幂等、消失的标的改状态而非删行。

需要真库——这几条断言问的是"行还在不在、状态对不对"，不是内存里的结构。
"""

from __future__ import annotations

import pytest

from keel.market.instruments import from_markets, sync_instruments

SPOT_OK = {
    "symbol": "BTC/USDT",
    "base": "BTC",
    "quote": "USDT",
    "spot": True,
    "active": True,
    "precision": {"price": 0.01, "amount": 1e-05},
}
ETH = {**SPOT_OK, "symbol": "ETH/USDT", "base": "ETH"}


class FakeExchange:
    def __init__(self, markets: list[dict]) -> None:
        self.markets = markets
        self.calls = 0

    def fetch_markets(self) -> list[dict]:
        self.calls += 1
        return self.markets


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
        conn.execute("delete from instruments")
        conn.commit()


def test_sync_writes_tradable_rows() -> None:
    from keel.db.pool import connection
    from keel.tools import backfill

    out = sync_instruments(FakeExchange([SPOT_OK]))
    assert out["upserted"] == 1
    # 关键断言：backfill 读的就是这张表，同步完必须能读到
    assert "BTC/USDT" in backfill.enabled_symbols()
    with connection() as conn:
        row = conn.execute("select status, quote from instruments").fetchone()
    assert row == {"status": "TRADING", "quote": "USDT"}


def test_sync_is_idempotent() -> None:
    exchange = FakeExchange([SPOT_OK, ETH])
    sync_instruments(exchange)
    sync_instruments(exchange)
    from keel.db.pool import connection

    with connection() as conn:
        n = conn.execute("select count(*) as n from instruments").fetchone()["n"]
    assert n == 2


def test_disappeared_symbol_is_halted_not_deleted() -> None:
    sync_instruments(FakeExchange([SPOT_OK, ETH]))
    out = sync_instruments(FakeExchange([SPOT_OK]))
    assert out["halted"] == 1
    from keel.db.pool import connection
    from keel.tools import backfill

    with connection() as conn:
        status = conn.execute(
            "select status from instruments where symbol = 'ETH/USDT'"
        ).fetchone()["status"]
    # 行还在（历史回测要能引用），但不再参与回补
    assert status == "HALT"
    assert backfill.enabled_symbols() == ["BTC/USDT"]


def test_non_usdt_pairs_are_not_written() -> None:
    eth_btc = {**SPOT_OK, "symbol": "ETH/BTC", "base": "ETH", "quote": "BTC"}
    out = sync_instruments(FakeExchange([eth_btc]))
    assert out["upserted"] == 0
    assert from_markets([eth_btc]) == []
