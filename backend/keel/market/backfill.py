"""历史回补（CCXT REST）。§3.2。

幂等靠"分区文件不可变 + plan 按已有覆盖裁剪"，不靠唯一约束：
原始湖是 Parquet，没有约束可用。重跑同一区间是安全的，plan 会让它变成空任务。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from keel.data.datasets import ODS_BARS
from keel.data.lake import list_partitions, partition_dir, read_bars, write_partition
from keel.market.normalize import interval_to_ms, normalize_ohlcv


def _utc(when: datetime) -> pd.Timestamp:
    """统一成带时区的 Timestamp。

    不能写 ``pd.Timestamp(when, tz="UTC")``：传进来的 datetime 若已带时区，
    pandas 会抛「Cannot pass a datetime or Timestamp with tzinfo with the tz parameter」。
    调用方给不带时区的 datetime 也应该能用（当成 UTC），所以这里两种都收。
    """
    stamp = pd.Timestamp(when)
    return stamp if stamp.tz is not None else stamp.tz_localize("UTC")


@dataclass(frozen=True, slots=True)
class BackfillJob:
    symbol: str
    interval: str
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class JobResult:
    job: BackfillJob
    rows_written: int
    batches: int
    last_open_time: datetime | None


def _existing(symbol: str, interval: str, root: Path) -> pd.DataFrame:
    return read_bars(root, symbol=symbol, interval=interval)


def _floor(stamp: pd.Timestamp, step: pd.Timedelta) -> pd.Timestamp:
    """把时间戳向下对齐到 K 线网格。

    epoch 是所有交易所共同的网格原点，bar 的 open_time 就是 step 的整数倍，
    所以向下取整即可。不做这一步的后果见 ``plan`` 的注释。
    """
    return pd.Timestamp((stamp.value // step.value) * step.value, tz="UTC")


def plan(jobs: list[BackfillJob], *, root: Path) -> list[BackfillJob]:
    """把请求裁剪成真正需要拉取的区间。已有数据不必重拉——这里省的是限流配额。

    三个边界约定，缺一个就会产生「看起来正常、行数却在涨」的重复写入：

    1. **区间要按 K 线网格对齐**。调用方给的是 ``now - 2 天``，秒级精度，
       几乎永远不落在整点上。不对齐就每次多算一个「xx:41 到 xx:00」的缺口，
       于是每次都重拉已经存在的那一根 bar；调度器每小时跑一次，一天写 24 遍。
    2. **``job.end`` 是闭区间**：它表示「最后一根想要的 K 线」，与 ``execute``
       的 ``end_ms + step`` 一致。两处约定不同时，差异会以「多一行」的形式出现。
    3. **只处理已收盘的 bar**。``floor(end)`` 是仍在形成的那根，减一个 step
       才是最后一根完整的。同步任务在 HH:05 触发，要的是 HH-1:00 那根。
    """
    out: list[BackfillJob] = []
    for job in jobs:
        step = pd.Timedelta(milliseconds=interval_to_ms(job.interval))
        must_start = _floor(_utc(job.start), step)
        must_end = _floor(_utc(job.end), step) - step
        if must_end < must_start:
            continue

        frame = _existing(job.symbol, job.interval, root)
        if frame.empty:
            out.append(
                BackfillJob(
                    job.symbol, job.interval, must_start.to_pydatetime(), must_end.to_pydatetime()
                )
            )
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        head = times.min()
        last = times.max()
        if must_start < head:
            out.append(
                BackfillJob(
                    job.symbol,
                    job.interval,
                    must_start.to_pydatetime(),
                    min(must_end, head - step).to_pydatetime(),
                )
            )
        if must_end > last:
            out.append(
                BackfillJob(
                    job.symbol,
                    job.interval,
                    max(must_start, last + step).to_pydatetime(),
                    must_end.to_pydatetime(),
                )
            )
    return [j for j in out if j.end >= j.start]


def execute(job: BackfillJob, *, exchange, root: Path, limit: int = 1000) -> JobResult:
    """分页拉到底。失败整体抛出，不做部分成功——半批次的数据最难判断。"""
    step_ms = interval_to_ms(job.interval)
    cursor_ms = int(job.start.timestamp() * 1000)
    # end 是闭区间（包含 job.end 这一根），与 plan 的约定一致。
    end_ms = int(job.end.timestamp() * 1000) + step_ms
    rows_written = 0
    batches = 0
    last_open: datetime | None = None
    while cursor_ms < end_ms:
        rows = exchange.fetch_ohlcv(job.symbol, job.interval, cursor_ms, limit=limit)
        if not rows:
            # 空返回不算一批：它表示「这段没有数据」，不是「又拉了一批」。
            # 计进去会把批次统计变成噪声，也会让限流预算估大。
            break
        batches += 1
        frame = normalize_ohlcv(rows, job.symbol, job.interval)
        frame = frame[frame["open_time"] <= _utc(job.end)]
        if frame.empty:
            break
        months = frame["open_time"].dt.tz_localize(None).dt.to_period("M")
        for _, chunk in frame.groupby(months):
            when = chunk["open_time"].iloc[0].to_pydatetime()
            path = partition_dir(root, ODS_BARS, job.symbol, job.interval, when)
            rows_written += write_partition(path, chunk)
        last_open = frame["open_time"].iloc[-1].to_pydatetime()
        next_ms = int(frame["open_time"].iloc[-1].timestamp() * 1000) + step_ms
        if next_ms <= cursor_ms:
            break
        cursor_ms = next_ms
    return JobResult(job=job, rows_written=rows_written, batches=batches, last_open_time=last_open)


def coverage_rows(root: Path) -> list[tuple[str, str, int, int]]:
    """给运维看的分区清单。真正的覆盖率计算在 ``keel.data.coverage``。"""
    return list_partitions(root, ODS_BARS)
