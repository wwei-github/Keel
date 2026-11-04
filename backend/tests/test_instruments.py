"""交易对主数据同步：只收现货 USDT 对，下线改状态不删行。"""

from __future__ import annotations

from keel.market.instruments import InstrumentRow, from_markets

SPOT_OK = {
    "symbol": "BTC/USDT",
    "base": "BTC",
    "quote": "USDT",
    "spot": True,
    "active": True,
    "precision": {"price": 0.01, "amount": 1e-05},
}
PERP = {
    "symbol": "BTC/USDT:USDT",
    "base": "BTC",
    "quote": "USDT",
    "spot": False,
    "active": True,
    "precision": {"price": 0.01, "amount": 0.001},
}
INACTIVE = {**SPOT_OK, "symbol": "OLD/USDT", "active": False}
BTC_QUOTE = {**SPOT_OK, "symbol": "ETH/BTC", "base": "ETH", "quote": "BTC"}


def test_keeps_only_spot_usdt_pairs() -> None:
    rows = from_markets([SPOT_OK, PERP, INACTIVE, BTC_QUOTE])
    assert [r.symbol for r in rows] == ["BTC/USDT"]


def test_precision_is_converted_to_decimals() -> None:
    (row,) = from_markets([SPOT_OK])
    assert (row.price_precision, row.qty_precision) == (2, 5)


def test_precision_falls_back_when_step_is_odd() -> None:
    weird = {**SPOT_OK, "precision": {"price": None, "amount": ""}}
    (row,) = from_markets([weird])
    assert (row.price_precision, row.qty_precision) == (8, 8)


def test_rows_are_sorted_so_upsert_order_is_stable() -> None:
    a = {**SPOT_OK, "symbol": "ETH/USDT", "base": "ETH"}
    b = {**SPOT_OK, "symbol": "ADA/USDT", "base": "ADA"}
    assert [r.symbol for r in from_markets([a, b])] == ["ADA/USDT", "ETH/USDT"]


def test_market_type_is_pinned_to_spot() -> None:
    (row,) = from_markets([SPOT_OK])
    assert isinstance(row, InstrumentRow)
    assert row.market_type == "spot"
    assert row.status == "TRADING"
