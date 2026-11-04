"""覆盖率与缺口分析。§3.1。

加密市场 7×24，没有休市，所以"应有 bar"就是一条连续的等差时间轴。
缺口分两级：单根缺失可以容忍（多半是交易所那一分钟没成交），连续两根以上
视为 blocking——连续缺失通常意味着采集中断，而采集中断意味着那段时间的回测结果是假的。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from keel.market.normalize import interval_to_ms

BLOCKING_CONSECUTIVE = 2


@dataclass(frozen=True, slots=True)
class Gap:
    start: datetime
    end: datetime
    missing: int
    severity: str


@dataclass(frozen=True, slots=True)
class Coverage:
    symbol: str
    interval: str
    start: datetime | None
    end: datetime | None
    expected: int
    present: int
    gaps: tuple[Gap, ...]

    @property
    def ratio(self) -> float:
        return 0.0 if not self.expected else self.present / self.expected

    @property
    def blocking_gaps(self) -> int:
        return sum(1 for g in self.gaps if g.severity == "blocking")


def analyse(frame: pd.DataFrame, interval: str) -> Coverage:
    step = pd.Timedelta(milliseconds=interval_to_ms(interval))
    symbol = str(frame["symbol"].iloc[0]) if len(frame) else ""
    if frame.empty:
        return Coverage(
            symbol=symbol,
            interval=interval,
            start=None,
            end=None,
            expected=0,
            present=0,
            gaps=(Gap(start=None, end=None, missing=0, severity="blocking"),),  # type: ignore[arg-type]
        )
    times = pd.to_datetime(frame["open_time"], utc=True).sort_values().reset_index(drop=True)
    grid = pd.date_range(times.iloc[0], times.iloc[-1], freq=step, tz="UTC")
    missing_idx = grid.difference(pd.DatetimeIndex(times))
    gaps: list[Gap] = []
    run_start: datetime | None = None
    prev: datetime | None = None
    for stamp in list(missing_idx) + [None]:
        if stamp is not None and (prev is None or stamp - prev == step):
            run_start = run_start or stamp
        else:
            if run_start is not None and prev is not None:
                missing = int((prev - run_start) / step) + 1
                severity = "blocking" if missing >= BLOCKING_CONSECUTIVE else "tolerated"
                gaps.append(Gap(start=run_start, end=prev, missing=missing, severity=severity))
            run_start = stamp
        prev = stamp
    return Coverage(
        symbol=symbol,
        interval=interval,
        start=times.iloc[0],
        end=times.iloc[-1],
        expected=len(grid),
        present=len(times),
        gaps=tuple(gaps),
    )
