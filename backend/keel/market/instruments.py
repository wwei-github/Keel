"""交易对主数据同步。§3.2。

这一步以前是缺的，后果很具体：``keel/tools/backfill.py`` 从 ``instruments`` 表
读启用标的，而没有任何代码往外写，所以 ``make backfill`` 与调度器的
``sync-closed-bars`` / ``daily-backfill`` 全部空转，只打一行
``no enabled instruments``。冷启动时看起来像"没有缺口"，实际是一根 K 线都没补。

两个设计取舍：

1. **只同步现货**（ADR-001）。永续在 V3，现在把合约写进主数据，回补器会去拉
   ``BTC/USDT:USDT`` 这种符号，而 V1 的清算与成本模型都不处理它。
2. **下线不删行，改状态**。``status`` 从 ``TRADING`` 变 ``BREAK``/``HALT``，
   因为历史回测要能引用已下线的标的——删掉行会让三年前的回测结果无法复现。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel.logging import get_logger

log = get_logger(__name__)

QUOTE = "USDT"


@dataclass(frozen=True, slots=True)
class InstrumentRow:
    instrument_id: str
    symbol: str
    base: str
    quote: str
    market_type: str
    status: str
    price_precision: int
    qty_precision: int


def _decimals(step: object, fallback: int = 8) -> int:
    """把 ccxt 的 precision（0.01 这种步长）换成小数位数。

    步长可能是 1e-05 这种浮点，直接 ``len(str(v).split(".")[-1])`` 会得到
    ``5`` 也可能得到 ``8``（取决于浮点表示），所以走 Decimal。
    算不出来时退回 8：宁可多留几位，也不要把价格截错。
    """
    try:
        exp = -Decimal(str(step)).as_tuple().exponent
    except Exception:  # noqa: BLE001 — 交易所字段格式随版本变，兜底比炸掉好
        return fallback
    return int(max(0, min(exp, 8)))


def from_markets(markets: list[dict]) -> list[InstrumentRow]:
    """从 ccxt 的 ``load_markets()`` 结果筛出现货 USDT 对。

    筛掉的四类各有原因：
    - ``spot`` 为假：V1 不做合约（ADR-001）
    - ``active`` 为假：已停止交易，补不到数据
    - quote 不是 USDT：另外申报价币种的回测没有对应的成本模型
    - 符号里带 ``:``：ccxt 用这种写法表示合约，现货永远是 ``BASE/QUOTE``
    """
    rows: list[InstrumentRow] = []
    for m in markets:
        symbol = str(m.get("symbol") or "")
        if not m.get("spot") or not m.get("active"):
            continue
        if m.get("quote") != QUOTE or ":" in symbol or "/" not in symbol:
            continue
        precision = m.get("precision") or {}
        rows.append(
            InstrumentRow(
                instrument_id=symbol,
                symbol=symbol,
                base=str(m.get("base") or symbol.split("/")[0]),
                quote=QUOTE,
                market_type="spot",
                status="TRADING",
                price_precision=_decimals(precision.get("price")),
                qty_precision=_decimals(precision.get("amount")),
            )
        )
    rows.sort(key=lambda r: r.symbol)
    return rows


UPSERT = """
insert into instruments (
    instrument_id, symbol, base, quote, market_type, status,
    price_precision, qty_precision, updated_at
) values (%s, %s, %s, %s, %s, %s, %s, %s, now())
on conflict (instrument_id) do update set
    base = excluded.base,
    quote = excluded.quote,
    market_type = excluded.market_type,
    status = excluded.status,
    price_precision = excluded.price_precision,
    qty_precision = excluded.qty_precision,
    updated_at = now()
"""


def sync_instruments(exchange=None, *, quote: str = QUOTE) -> dict[str, int]:
    """拉一次交易对主数据并落库。

    返回 ``{"seen", "upserted", "halted"}``。``halted`` 是本次被标为停止交易
    的标的数——它不该很大，连续几天都很大说明交易所那边在批量下架，
    这时候该先看一眼再去补数据。
    """
    from keel.db.pool import transaction

    if exchange is None:
        from keel.market.exchange import BinanceSpot

        exchange = BinanceSpot()

    markets = exchange.fetch_markets()
    rows = from_markets(markets)
    if quote != QUOTE:
        rows = [r for r in rows if r.quote == quote]

    upserted = 0
    halted = 0
    with transaction() as conn:
        for row in rows:
            conn.execute(
                UPSERT,
                (
                    row.instrument_id,
                    row.symbol,
                    row.base,
                    row.quote,
                    row.market_type,
                    row.status,
                    row.price_precision,
                    row.qty_precision,
                ),
            )
            upserted += 1
        # 本次没出现的现货对，标为停止交易而不是删掉：
        # 删行会让引用它的历史回测无法复现。
        seen = [r.instrument_id for r in rows]
        if seen:
            cur = conn.execute(
                """
                update instruments set status = 'HALT', updated_at = now()
                where market_type = 'spot' and status = 'TRADING'
                  and instrument_id <> all(%s)
                """,
                (seen,),
            )
            halted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    result = {"seen": len(markets), "upserted": upserted, "halted": halted}
    log.info("instruments synced", extra=result)
    return result
