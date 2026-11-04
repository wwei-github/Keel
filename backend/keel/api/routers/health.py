"""健康检查（API-11）。

唯一在骨架阶段就返回真实结果的接口——否则容器编排的 healthcheck 无从写起。
它只报进程活着与版本，不探数据库：探数据库的健康检查会在 PG 重启时
把 API 一起判死，反而放大故障。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from keel import __version__
from keel.api.deps import API_PREFIX, require_token
from keel.api.schemas import Envelope, HealthData, Meta

router = APIRouter(prefix=API_PREFIX, tags=["ops"])


@router.get("/health", response_model=Envelope[HealthData], dependencies=[Depends(require_token)])
def health() -> Envelope[HealthData]:
    return Envelope(
        data=HealthData(status="ok", version=__version__, components={}),
        meta=Meta(),
    )
