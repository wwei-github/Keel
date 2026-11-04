"""降采样。语义在 §3.11 固定，前端不得二次聚合。

open=桶内首值、high=max、low=min、close=末值、volume=sum，
``revision`` 取桶内最大值（最保守）。桶边界对齐自然周期。
"""

from __future__ import annotations

import pandas as pd


def downsample(frame: pd.DataFrame, bucket_ms: int) -> pd.DataFrame:
    if frame.empty or bucket_ms <= 0:
        return frame
    work = frame.copy()
    # 必须用 dt.floor，不要手写整数整除：
    #   frame["open_time"].astype("int64") 在 pandas 2.x 里给的是**微秒**，
    #   再 //10**6 得到秒、再 */bucket_ms 得到的是毫秒数被当成 …… 最后时间会掉到 1970 年。
    #   这个错误不会报错，只会让图表的横轴离谱十年。
    work["bucket"] = work["open_time"].dt.floor(f"{bucket_ms}ms")
    grouped = work.groupby("bucket", sort=True)
    # 只聚合传入的列：API 侧的查询不一定带 symbol/interval
    # （选择列越少 DuckDB 读得越少），硬要求它们会让调用方为了函数签名多查几列。
    spec: dict[str, pd.Series] = {"open_time": grouped["bucket"].first()}
    for column in ("symbol", "interval"):
        if column in work.columns:
            spec[column] = grouped[column].first()
    spec.update(
        {
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(),
        }
    )
    if "revision" in work.columns:
        spec["revision"] = grouped["revision"].max()
    return pd.DataFrame(spec).reset_index(drop=True)
