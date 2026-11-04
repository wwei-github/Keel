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
from keel.features import FEATURE_VERSION

ALL = ("dwd_bars", "dws_features", "ops_coverage")


@dataclass(frozen=True, slots=True)
class EtlResult:
    dataset: str
    rows_written: int
    started_at: datetime
    finished_at: datetime
    mart_version: str


def _build_features(dwd: pd.DataFrame) -> pd.DataFrame:
    """由 ``dwd_bars`` 算特征表。

    必须**按 (symbol, interval) 分组**再算。原先整表一次性算，两个后果：
    ``open_time`` 作为唯一索引会撞车（两个标的同一时刻各一行，索引重复，
    写进 DuckDB 后无法区分谁是谁），而且滚动窗口会跨标的续算——BTC 的最后
    20 根会参与 ETH 的 SMA，算出来的特征两样都不对。

    特征列由 ``keel.features.compute`` 决定，这里不写死列名：换特征版本时
    只改那一处。
    """
    if dwd.empty:
        return pd.DataFrame(columns=["open_time", "symbol", "interval", "feature_version"])

    parts: list[pd.DataFrame] = []
    for (symbol, interval), chunk in dwd.groupby(["symbol", "interval"], sort=True):
        # 只取已收盘的 bar 参与计算；is_outlier 的 bar 仍然保留——它是数据质量
        # 记录，不是"这条数据不存在"，丢掉会让覆盖率与回测都少一行。
        ordered = chunk.sort_values("open_time")
        frame = compute_batch(ordered.set_index("open_time"))
        frame = frame.reset_index()
        frame["symbol"] = symbol
        frame["interval"] = interval
        frame["feature_version"] = FEATURE_VERSION
        parts.append(frame)
    return pd.concat(parts, ignore_index=True)


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
                feats = _build_features(dwd)
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
