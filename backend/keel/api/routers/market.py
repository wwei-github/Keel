"""行情查询接口（API-01 K 线、API-02 覆盖范围）。

实现留到 ETL 落地之后。这里先把路由与签名固定下来，前端可以照此联调：
形状错了改起来远比加实现贵。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from keel.api.deps import API_PREFIX, require_token
from keel.api.schemas import Envelope
from keel.errors import NotImplementedYet

router = APIRouter(prefix=API_PREFIX, tags=["market"], dependencies=[Depends(require_token)])


@router.get("/bars")
def get_bars(
    symbol: Annotated[str, Query(description="如 BTC/USDT")],
    interval: Annotated[str, Query(description="1m/5m/15m/1h/4h/1d")],
    start: Annotated[str, Query(description="ISO8601，含")],
    end: Annotated[str, Query(description="ISO8601，含")],
    bucket_ms: Annotated[int | None, Query(description="降采样桶宽；不传则不降采样")] = None,
) -> Envelope[list]:
    """API-01。

    降采样语义在 §3.11 固定：open=首值、high=max、low=min、close=末值、volume=sum，
    桶边界对齐自然周期。前端不得再聚合一次。
    """
    raise NotImplementedYet("API-01 get_bars 尚未实现（等待 ETL 与 dwd_bars）")


@router.get("/coverage")
def get_coverage(
    symbol: Annotated[str, Query()],
    interval: Annotated[str, Query()],
) -> Envelope[dict]:
    """API-02：返回覆盖区间与缺口清单（blocking / tolerated 两级）。"""
    raise NotImplementedYet("API-02 get_coverage 尚未实现")
