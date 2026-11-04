"""行情查询接口（API-01 K 线、API-02 覆盖范围）。"""

from __future__ import annotations

from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, Query

from keel.api.deps import API_PREFIX, require_token
from keel.api.downsample import downsample
from keel.api.schemas import Bar, Envelope, Meta
from keel.db.mart import connect
from keel.market.normalize import interval_to_ms

router = APIRouter(prefix=API_PREFIX, tags=["market"], dependencies=[Depends(require_token)])

MAX_POINTS_CAP = 10_000


@router.get("/bars", response_model=Envelope[list[Bar]])
def get_bars(
    symbol: Annotated[str, Query(description="如 BTC/USDT")],
    interval: Annotated[str, Query(description="1m/5m/15m/1h/4h/1d")],
    start: Annotated[str, Query(alias="from", description="ISO8601，含")],
    end: Annotated[str, Query(alias="to", description="ISO8601，含")],
    limit: Annotated[int, Query(ge=1, le=50_000)] = 5000,
    max_points: Annotated[int, Query(ge=2, le=MAX_POINTS_CAP)] = 2000,
) -> Envelope[list[Bar]]:
    with connect() as con:
        frame = con.execute(
            """select open_time, open, high, low, close, volume, revision
                 from dwd_bars
                where symbol = ? and interval = ?
                  and open_time >= ? and open_time <= ?
                order by open_time
                limit ?""",
            [symbol, interval, pd.Timestamp(start), pd.Timestamp(end), limit],
        ).df()
    # DuckDB 的 timestamptz 按会话时区返回（本机是 +08:00）。接口约定是 UTC，
    # 这里统一转一次——否则同一份数据在 UTC 机器与东八区机器上返回不同字符串。
    if len(frame):
        frame["open_time"] = frame["open_time"].dt.tz_convert("UTC")
    step = interval_to_ms(interval)
    if len(frame) > max_points:
        bucket = step * -(-len(frame) // max_points)
        frame = downsample(frame, bucket)
    data = [
        Bar(
            open_time=row.open_time.to_pydatetime(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in frame.itertuples()
    ]
    as_of = frame["open_time"].max().to_pydatetime() if len(frame) else None
    return Envelope(data=data, meta=Meta(as_of=as_of, stale=False))


@router.get("/coverage")
def get_coverage(
    symbol: Annotated[str, Query()],
    interval: Annotated[str, Query()],
) -> Envelope[dict]:
    with connect() as con:
        rows = con.execute(
            'select symbol, interval, start, "end", expected, present, blocking_gaps, gaps '
            "from ops_coverage where symbol = ? and interval = ?",
            [symbol, interval],
        ).df()
    if rows.empty:
        return Envelope(data={"symbol": symbol, "interval": interval, "gaps": []}, meta=Meta())
    row = rows.iloc[0]
    import json

    # 同上：DuckDB 按会话时区返回 timestamptz，接口约定是 UTC。
    # 不转的话东八区机器返回 +08:00，前端按 UTC 解析就会差 8 小时。
    def _utc(value):
        return pd.Timestamp(value).tz_convert("UTC").to_pydatetime() if value is not None else None

    return Envelope(
        data={
            "symbol": row["symbol"],
            "interval": row["interval"],
            "start": _utc(row["start"]),
            "end": _utc(row["end"]),
            "expected": int(row["expected"]),
            "present": int(row["present"]),
            "blocking_gaps": int(row["blocking_gaps"]),
            "gaps": json.loads(row["gaps"]),
        },
        meta=Meta(as_of=None),
    )
