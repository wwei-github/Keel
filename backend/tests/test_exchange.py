"""CCXT 适配层：分页上限、限流、错误映射。真网络测试默认跳过。"""

from __future__ import annotations

import os

import pytest

from keel.market.exchange import MAX_LIMIT, BinanceSpot


class FakeClient:
    """记下每次调用参数，按 ``pages`` 依次返回。"""

    def __init__(self, pages: list[list[list[float]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str, int | None, int]] = []

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        self.calls.append((symbol, timeframe, since, limit))
        return self.pages.pop(0) if self.pages else []


def _sleeps() -> list[float]:
    return []


def test_limit_is_capped() -> None:
    fake = FakeClient([[]])
    ex = BinanceSpot(client=fake, rps=1000, sleep=_sleeps().append)
    ex.fetch_ohlcv("BTC/USDT", "1h", 0, limit=5000)
    assert fake.calls[0][3] == MAX_LIMIT


def test_rate_limiter_spaces_calls() -> None:
    slept: list[float] = []
    fake = FakeClient([[], [], []])
    ex = BinanceSpot(client=fake, rps=2.0, sleep=slept.append)
    for _ in range(3):
        ex.fetch_ohlcv("BTC/USDT", "1h", 0)
    assert len(fake.calls) == 3
    # rps=2 → 间隔 0.5s，三次调用至少要有两次等待
    assert len(slept) == 2
    assert all(abs(s - 0.5) < 0.2 for s in slept)


def test_network_error_is_mapped() -> None:
    class Boom(FakeClient):
        def fetch_ohlcv(self, *a, **kw):
            raise ConnectionError("dns 挂了")

    ex = BinanceSpot(client=Boom([]), rps=1000, sleep=lambda _s: None)
    from keel.errors import ErrorCode, KeelError

    with pytest.raises(KeelError) as info:
        ex.fetch_ohlcv("BTC/USDT", "1h", 0)
    assert info.value.code is ErrorCode.EXCHANGE_UNAVAILABLE


@pytest.mark.skipif(not os.environ.get("KEEL_LIVE_EXCHANGE"), reason="需要 KEEL_LIVE_EXCHANGE=1")
def test_live_binance_returns_bars() -> None:
    ex = BinanceSpot()
    rows = ex.fetch_ohlcv("BTC/USDT", "1h", since_ms=1_700_000_000_000, limit=5)
    assert rows and len(rows) <= 5
    assert len(rows[0]) == 6
