"""Parquet 与 DuckDB 之间的列契约。

列顺序与类型在这里定死。写的时候按 schema cast，读的时候不信 Parquet 自带的类型——
一个用 int64 存 volume 的分区会让下游在某个 float 除法上炸掉，而报错信息指向别处。
"""

from __future__ import annotations

import pyarrow as pa

BARS_SCHEMA = pa.schema(
    [
        ("open_time", pa.timestamp("us", tz="UTC")),
        ("symbol", pa.string()),
        ("interval", pa.string()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("revision", pa.int32()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)

BARS_COLUMNS = tuple(BARS_SCHEMA.names)
