"""Binance 现货适配器。§3.2。

限流放在这一层，不放在调用点：调用点散落的 ``sleep`` 最后一定会被谁删掉一个，
然后在某个周末被封 IP。实测（2026-11-05）：单次 K 线上限 1000 根，
``rateLimit`` 50ms；按权重算稳态约 10 次/秒，这里取 10 作为默认值。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from keel.config import get_settings
from keel.errors import ErrorCode, KeelError

MAX_LIMIT = 1000
RATE_LIMIT_RPS = 10.0


class ExchangeUnavailable(KeelError):
    code = ErrorCode.EXCHANGE_UNAVAILABLE
    http_status = 503


class ExchangeBadSymbol(KeelError):
    code = ErrorCode.NOT_FOUND
    http_status = 404


class BinanceSpot:
    """只做现货（ADR-001）。没有杠杆、没有资金费、没有保证金。"""

    def __init__(
        self,
        client: Any | None = None,
        *,
        rps: float = RATE_LIMIT_RPS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if client is None:
            import ccxt

            settings = get_settings()
            client = ccxt.binance(
                {
                    "enableRateLimit": True,
                    "apiKey": settings.exchange_api_key or None,
                    "secret": settings.exchange_api_secret or None,
                    "options": {"defaultType": "spot"},
                }
            )
        self._client = client
        self._interval = 1.0 / rps
        self._sleep = sleep
        self._last = 0.0

    def _throttle(self) -> None:
        now = time.monotonic()
        delta = now - self._last
        if self._last and delta < self._interval:
            self._sleep(self._interval - delta)
        self._last = time.monotonic()

    def fetch_ohlcv(
        self, symbol: str, interval: str, since_ms: int, limit: int = MAX_LIMIT
    ) -> list[list[float]]:
        self._throttle()
        try:
            return (
                self._client.fetch_ohlcv(
                    symbol, timeframe=interval, since=int(since_ms), limit=min(limit, MAX_LIMIT)
                )
                or []
            )
        except Exception as exc:  # ccxt 的异常树随版本变，按语义分两类就够
            name = type(exc).__name__
            if "BadSymbol" in name or "Invalid" in name:
                raise ExchangeBadSymbol(f"bad symbol: {symbol}") from exc
            raise ExchangeUnavailable(f"exchange call failed: {name}: {exc}") from exc

    def fetch_markets(self) -> list[dict]:
        self._throttle()
        try:
            return list(self._client.load_markets().values())
        except Exception as exc:
            raise ExchangeUnavailable(f"load_markets failed: {exc}") from exc
