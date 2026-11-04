"""数据集定义。§5.4。

数据集的边界就是"表命名空间"的边界：``ods_`` 原始、``dwd_`` 明细、``dws_`` 汇总、
``ops_`` 运维。名字前缀不是风格，是让"这张表能不能删"一眼可判——
所有 ``ods_``/``dwd_``/``dws_`` 都能从上游重建，``ops_`` 不能。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Dataset:
    name: str
    layer: str
    primary_key: tuple[str, ...]
    partition_keys: tuple[str, ...]
    append_only: bool
    description: str


ODS_BARS = Dataset(
    name="ods_bars",
    layer="ods",
    primary_key=("symbol", "interval", "open_time"),
    partition_keys=("symbol", "interval", "year", "month"),
    append_only=True,
    description="原始 K 线，按采集/回填批次落 Parquet，永不变更已有分区",
)

DWD_BARS = Dataset(
    name="dwd_bars",
    layer="dwd",
    primary_key=("symbol", "interval", "open_time"),
    partition_keys=("symbol", "interval"),
    append_only=False,
    description="清洗后的 K 线：去重、补齐交易日历、标记异常 bar",
)

DWS_FEATURES = Dataset(
    name="dws_features",
    layer="dws",
    primary_key=("symbol", "interval", "open_time", "feature_version"),
    partition_keys=("interval",),
    append_only=False,
    description="特征集市。主键含 feature_version——换公式就是新一行，不覆盖旧值",
)

OPS_COVERAGE = Dataset(
    name="ops_coverage",
    layer="ops",
    primary_key=("symbol", "interval", "start", "end"),
    partition_keys=(),
    append_only=False,
    description="覆盖区间与缺口登记，blocking/tolerated 两级（§3.1）",
)

ALL_DATASETS = (ODS_BARS, DWD_BARS, DWS_FEATURES, OPS_COVERAGE)
