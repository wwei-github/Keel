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


def plan(jobs: list[BackfillJob], *, root: Path) -> list[BackfillJob]:
    """把请求裁剪成真正需要拉取的区间。已有数据不必重拉——这里省的是限流配额。"""
    step = pd.Timedelta(milliseconds=interval_to_ms(jobs[0].interval)) if jobs else None
    out: list[BackfillJob] = []
    for job in jobs:
        frame = _existing(job.symbol, job.interval, root)
        if frame.empty:
            out.append(job)
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        head = times.min().to_pydatetime()
        tail = times.max().to_pydatetime() + (step.to_pytimedelta() if step else None)
        if job.start < head:
            out.append(BackfillJob(job.symbol, job.interval, job.start, min(job.end, head)))
        if job.end > tail:
            out.append(BackfillJob(job.symbol, job.interval, max(job.start, tail), job.end))
    return out


def execute(job: BackfillJob, *, exchange, root: Path, limit: int = 1000) -> JobResult:
    """分页拉到底。失败整体抛出，不做部分成功——半批次的数据最难判断。"""
    step_ms = interval_to_ms(job.interval)
    cursor_ms = int(job.start.timestamp() * 1000)
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
        frame = frame[frame["open_time"] < _utc(job.end) + pd.Timedelta(milliseconds=step_ms)]
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
