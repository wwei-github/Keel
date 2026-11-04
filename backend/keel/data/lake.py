"""Parquet 原始湖读写。

三条写规则：
1. 每个分区写一个新文件 ``part-<uuid>.parquet``，不做原地覆盖——ODS 是只追加的。
2. 先写 ``.tmp``，``os.replace`` 原子改名。中途崩溃只会留下没人读的 tmp 文件。
3. 写之前按 ``BARS_SCHEMA`` cast，列不齐就报错，不允许 Parquet 自由生长。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq

from keel.data.datasets import Dataset
from keel.data.schemas import BARS_COLUMNS, BARS_SCHEMA


def quote_symbol(symbol: str) -> str:
    """``BTC/USDT`` → ``BTC_USDT``。只影响目录名；symbol 列里存的永远是原值。"""
    return symbol.replace("/", "_")


def unquote_symbol(part: str) -> str:
    """``BTC_USDT`` → ``BTC/USDT``。仅用于展示，正确性不依赖它。"""
    return part.replace("_", "/", 1)


def partition_dir(root: Path, dataset: Dataset, symbol: str, interval: str, when) -> Path:
    keys = dataset.partition_keys
    parts = {"symbol": f"symbol={quote_symbol(symbol)}", "interval": f"interval={interval}"}
    parts["year"] = f"year={when.year}"
    parts["month"] = f"month={when.month:02d}"
    path = Path(root) / dataset.name
    for key in keys:
        path = path / parts[key]
    return path


def write_partition(path: Path, frame: pd.DataFrame) -> int:
    missing = [c for c in BARS_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    table = pa.Table.from_pandas(frame[list(BARS_COLUMNS)], preserve_index=False)
    table = table.cast(BARS_SCHEMA)
    path.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    tmp = path / f"part-{token}.parquet.tmp"
    final = path / f"part-{token}.parquet"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, final)
    return table.num_rows


def _files(root: Path, dataset: Dataset, symbol: str | None, interval: str | None) -> list[Path]:
    base = Path(root) / dataset.name
    if symbol is not None:
        base = base / f"symbol={quote_symbol(symbol)}"
    pattern = "**/*.parquet"
    files = sorted(base.glob(pattern)) if base.exists() else []
    if interval is not None:
        files = [f for f in files if f"interval={interval}" in f.parts]
    return files


def read_bars(root: Path, symbol: str | None = None, interval: str | None = None) -> pd.DataFrame:
    """读回原始 K 线，每根 bar 只保留最新 revision。

    空湖返回空 DataFrame（带列），让调用方少写一个 if。
    """
    from keel.data.datasets import ODS_BARS

    files = _files(Path(root), ODS_BARS, symbol, interval)
    if not files:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in BARS_COLUMNS})
    # 必须关掉 Hive 分区推断。目录名是 symbol=BTC_USDT，pyarrow 默认会把
    # 目录名当成 symbol 列的值注入，于是读回来的 symbol 变成 "BTC_USDT"，
    # 与写入时的 "BTC/USDT" 不一致——API 按 "BTC/USDT" 查就查不到任何东西。
    # 分区是给路径用的，列值以文件内容为准。
    table = pq.read_table(files, schema=BARS_SCHEMA, partitioning=pads.partitioning(pa.schema([])))
    frame = table.to_pandas()
    if frame.empty:
        return frame
    # 同一 (symbol, interval, open_time) 可能有多份：ODS 是只追加的，回填修正、
    # 交易所修订、重跑都各写一个文件。读者要的是"每根 bar 一行"，不是文件内容。
    # 去重放在读侧而不是写侧，是因为写侧一旦覆盖就丢了修订历史（§5.5 要求
    # revision 不覆盖）。按 ingested_at/revision 取最新。
    frame = frame.sort_values(["ingested_at", "revision"])
    frame = frame.drop_duplicates(["symbol", "interval", "open_time"], keep="last")
    return frame.sort_values("open_time").reset_index(drop=True)


def list_partitions(root: Path, dataset: Dataset) -> list[tuple[str, str, int, int]]:
    base = Path(root) / dataset.name
    if not base.exists():
        return []
    out: list[tuple[str, str, int, int]] = []
    for d in sorted(base.glob("symbol=*/interval=*/year=*/month=*")):
        symbol = unquote_symbol(d.parent.parent.parent.name.split("=", 1)[1])
        interval = d.parent.parent.name.split("=", 1)[1]
        year = int(d.parent.name.split("=", 1)[1])
        month = int(d.name.split("=", 1)[1])
        out.append((symbol, interval, year, month))
    return out
