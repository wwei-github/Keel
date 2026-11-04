"""ETL 流水线。§3.3。

三条硬约束，都在实现时会被诱惑着放松：

1. **单写者**：DuckDB 同时只允许一个写进程。用 flock + 进程内单线程双保险，
   拿到锁失败就直接失败并告警，不排队重试——排队只会把问题推迟到更难查的时候。
2. **发布是原子的**：写临时文件 → 校验行数与 schema → 原子替换 → 发
   ``mart.published.v1``。中间失败就什么都没有，不存在"半个集市"。
3. **可重建**：集市里任何东西都能从原始湖重建。所以集市里不放"只在集市里有"的信息。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from keel.data.coverage import analyse
from keel.data.dwd import to_dwd
from keel.data.features import compute_batch
from keel.data.lake import read_bars
from keel.db.mart import publish as mart_publish
from keel.db.mart import writer_lock as mart_writer_lock

ALL = ("dwd_bars", "dws_features", "ops_coverage")


@dataclass(frozen=True, slots=True)
class EtlResult:
    dataset: str
    rows_written: int
    started_at: datetime
    finished_at: datetime
    mart_version: str


def run_once(
    datasets: list[str] | None = None,
    *,
    root: Path | None = None,
    mart: Path | None = None,
) -> list[EtlResult]:
    """重建集市。整个过程在写者锁内，最后原子改名发布。"""
    from keel.config import get_settings

    settings = get_settings()
    raw_root = Path(root or settings.raw_root)
    target = Path(mart or settings.duckdb_path)
    wanted = list(dict.fromkeys(datasets or ALL))
    unknown = [d for d in wanted if d not in ALL]
    if unknown:
        raise ValueError(f"unknown dataset(s): {unknown}; known: {list(ALL)}")
    started = datetime.now(UTC)
    counts: dict[str, int] = {}

    def build(tmp: Path) -> None:
        import duckdb

        ods = read_bars(raw_root)
        dwd = to_dwd(ods)
        con = duckdb.connect(str(tmp))
        try:
            con.register("dwd_df", dwd)
            con.execute("create table dwd_bars as select * from dwd_df")
            counts["dwd_bars"] = len(dwd)

            if "dws_features" in wanted:
                feats = (
                    compute_batch(dwd.set_index("open_time")) if not dwd.empty else (pd.DataFrame())
                )
                if not dwd.empty:
                    feats = feats.reset_index()
                con.register("feat_df", feats)
                con.execute("create table dws_features as select * from feat_df")
                counts["dws_features"] = len(feats)

            if "ops_coverage" in wanted:
                rows = []
                if not dwd.empty:
                    for (symbol, interval), chunk in dwd.groupby(["symbol", "interval"]):
                        cov = analyse(chunk, interval)
                        rows.append(
                            {
                                "symbol": symbol,
                                "interval": interval,
                                "start": cov.start,
                                "end": cov.end,
                                "expected": cov.expected,
                                "present": cov.present,
                                "blocking_gaps": cov.blocking_gaps,
                                "gaps": json.dumps([asdict(g) for g in cov.gaps], default=str),
                            }
                        )
                cov_df = pd.DataFrame(
                    rows,
                    columns=[
                        "symbol",
                        "interval",
                        "start",
                        "end",
                        "expected",
                        "present",
                        "blocking_gaps",
                        "gaps",
                    ],
                )
                con.register("cov_df", cov_df)
                con.execute("create table ops_coverage as select * from cov_df")
                counts["ops_coverage"] = len(cov_df)

            meta = pd.DataFrame(
                [
                    {
                        "dataset": name,
                        "mart_version": "",
                        "row_count": counts.get(name, 0),
                        "built_at": datetime.now(UTC),
                    }
                    for name in wanted
                ]
            )
            con.register("meta_df", meta)
            con.execute("create table ds_meta as select * from meta_df")
            counts["ds_meta"] = len(meta)
        finally:
            con.close()

    with mart_writer_lock(target.parent):
        version = mart_publish(build, target)
    finished = datetime.now(UTC)
    return [
        EtlResult(
            dataset=name,
            rows_written=counts.get(name, 0),
            started_at=started,
            finished_at=finished,
            mart_version=version,
        )
        for name in wanted
    ]
