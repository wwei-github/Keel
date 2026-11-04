"""交易所适配器工厂。

只在工厂里读 settings：适配器本身不该知道配置怎么来（§3.2 的「限流写在适配层」
同理——把环境依赖集中到一个地方，测试就不必到处 patch 环境变量）。
"""

from __future__ import annotations


def make_exchange():
    from keel.config import get_settings

    settings = get_settings()
    if settings.exchange_id != "binance" or settings.exchange_market_type != "spot":
        raise ValueError(
            f"V1 只支持 binance spot（ADR-001），当前配置 {settings.exchange_id}/"
            f"{settings.exchange_market_type}"
        )
    from keel.market.exchange import BinanceSpot

    # V1 的公开 K 线接口不需要 Key。带 Key 的构造方式留给 V2 的私有接口，
    # 现在传进去只会让人以为已经在用鉴权了。
    return BinanceSpot()
