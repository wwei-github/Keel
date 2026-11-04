"""ETL 流水线。§3.3。

三条硬约束，都在实现时会被诱惑着放松：

1. **单写者**：DuckDB 同时只允许一个写进程。用 flock + 进程内单线程双保险，
   拿到锁失败就直接失败并告警，不排队重试——排队只会把问题推迟到更难查的时候。
2. **发布是原子的**：写临时文件 → 校验行数与 schema → 原子替换 → 发
   ``mart.published.v1``。中间失败就什么都没有，不存在"半个集市"。
3. **可重建**：集市里任何东西都能从原始湖重建。所以集市里不放"只在集市里有"的信息。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EtlResult:
    dataset: str
    rows_written: int
    started_at: datetime
    finished_at: datetime
    mart_version: str


def run_once(datasets: list[str] | None = None) -> list[EtlResult]:
    """跑一轮 ETL。``datasets=None`` 表示全部。

    待实现：
    - 读 ``ods_*`` → 清洗 → 写 ``dwd_*``
    - 调 ``keel.features.compute`` → 写 ``dws_features``
    - 更新 ``ops_coverage``
    - 更新 ``mart.published.v1`` 所需的 etl_version
    """
    raise NotImplementedError("ETL 尚未实现：等待 Parquet 落盘原语与 DuckDB 连接层")
