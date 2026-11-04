# Keel V1（研究回测版）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把骨架填成能跑通的 V1：Binance 现货 1h/4h/1d 数据回填 → 清洗 → 特征 → 逐 bar 回测 → 报告 → 网页查看，并达到 `docs/architecture-design.md` §11.1 的 A1–A10。

**Architecture:** 原始 K 线落 Parquet 湖（`/data/raw`），ETL 在**唯一写者**约束下重建 DuckDB 集市（`dwd_bars`/`dws_features`/`ops_coverage`），回测是独立进程从 PostgreSQL 认领作业、逐 bar 调用 `keel.contracts` 的 `decide()`、把结果与制品写回 PG 与 `/data/artifacts`。API 只读集市 + 只入队，不跑回测。

**Tech Stack:** Python 3.11 / uv / FastAPI / Pydantic v2 / pandas / pyarrow / DuckDB 1.5 / PostgreSQL 16（psycopg 3 + psycopg_pool）/ ccxt / QuantStats / pytest + hypothesis / React + Vite + TypeScript + lightweight-charts。

## Global Constraints

以下为全项目约束，每个 task 隐含包含：

- Python `>=3.11,<3.13`；后端工作目录 `backend/`，命令一律 `UV_CACHE_DIR="$PWD/../.uv-cache" uv run …`。
- 一切时间列 UTC（`timestamptz` / `pd.Timestamp(tz="UTC")`）；容器 `TZ=UTC`。**禁止** `datetime.now()`，用 `datetime.now(UTC)`。
- 交易所只有 Binance 现货（ADR-001）；`exchange_market_type="spot"` 固定，永续相关代码一律不写。
- DuckDB **单写者**：任何写 DuckDB 的进程必须先拿 `data/duckdb/.writer.lock` 的 `flock`，拿不到直接失败（`EtlBusy`），不排队。
- 发布原子：新集市先建在 `*.duckdb.tmp`，校验通过后 `os.replace`。
- API 前缀 `/api/v1`；成功 `{data, meta{request_id, as_of, stale}}`；失败 `{error{code, message, details}, request_id}`；列表分页 `limit` 默认 100 上限 1000。
- 回测成本必须来自 `cost_models` 表，不接受调用方传费率（ADR-005）。
- 年化因子显式传：`1m=525600, 5m=105120, 15m=35040, 1h=8760, 4h=2190, 1d=365`；无风险利率 `0`。
- A3 的数值门槛是 `1e-9` 逐值一致（`numpy.testing.assert_allclose(atol=1e-9)`）。
- 风格：`ruff` line-length 100，`select = ["E","F","I","UP","B","SIM"]`，`ruff format` 必须干净。
- 提交信息用 `feat(scope): 中文说明` / `test(scope): …` / `docs: …`。

## 开工前的四个决策（已按文档推导，若不同意先改这里）

| 编号 | 决策 | 理由 | 影响 |
| --- | --- | --- | --- |
| D1 | `ops_coverage` 改为**派生表**：由 ETL 从原始湖算出，回填进程不再写它 | 回填进程与 ETL 都要写覆盖表，就成了两个 DuckDB 写者，违反单写者约束；覆盖区间本来就能从 Parquet 分区推出来 | 文档 §5.4 那句「`ops_` 不能从上游重建」要改；`coverage-check` 并入 ETL |
| D2 | V1 用自建逐 bar 执行器 `keel-bar`，Freqtrade 适配推迟到 V1.5 | Freqtrade 的策略接口是 `IStrategy.populate_*`，与我们的 `decide()` 契约不同，适配层是一整块独立工作；而 A3 真正要防的风险是「批量算的特征」与「决策时现算的特征」漂移，自建执行器能直接验证这一点 | A3 的表述要改：`dws_features` 与 `keel.features.compute` 现算值逐值一致（1e-9）；`Engine` 枚举加 `KEEL_BAR` |
| D3 | 需要 PostgreSQL 的测试通过 `KEEL_TEST_PG_DSN` 开关，未设置则 `pytest.skip` | 本机没有原生 PG，只有 Docker（daemon 时有时无）；单元测试不该被基础设施绑架 | `make pg-up` 起容器；CI 里起 service 容器 |
| D4 | API 时间参数用文档的 `from` / `to`（Python 侧 `start`/`end` + `Query(alias=...)`），`universe` 是列表 | 文档 §6.3 是协议权威，骨架里的 `start`/`end` 是占位 | 改 `backend/tests/test_api.py` 里两处参数名 |

---

## 里程碑

| 里程碑 | Tasks | 交付物 | 对应验收 |
| --- | --- | --- | --- |
| M0 基础设施 | 1–3 | PG 连接层、Parquet 湖原语、DuckDB 集市发布 | — |
| M1 数据回填 | 4–8 | CCXT 适配器、归一化、回填、缺口与覆盖 | A1、A2 |
| M2 特征集市 | 9 | `run_once` 产出 `dwd_bars`/`dws_features`/`ops_coverage` | A3 前半 |
| M3 回测 | 10–14 | 策略注册、逐 bar 执行器、指标、作业队列 | A3 后半、A4、A5 |
| M4 报告 | 15 | QuantStats 报告与制品 | A8 |
| M5 API 与 Web | 16–18 | API-01/02/03/04/05/10/11/12 + 三页前端 | A6 |
| M6 运维与验收 | 19–21 | 调度、心跳、告警、验收脚本 | A7、A9、A10 |

---

## Task 1: PostgreSQL 模式与连接层

**Files:**
- Modify: `backend/keel/db/schema.sql`（补四张表与 `backtest_runs` 缺列）
- Modify: `backend/keel/db/migrations.py:20-22`（`migrate`）
- Create: `backend/keel/db/pool.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_db.py`
- Modify: `Makefile`（加 `pg-up` / `pg-down`）

**Interfaces:**
- Consumes: `get_settings().pg_dsn`
- Produces: `keel.db.migrations.migrate(dsn: str | None = None) -> None`；`keel.db.pool.connection() -> Iterator[psycopg.Connection]`（`row_factory=dict_row`）；`keel.db.pool.transaction() -> Iterator[psycopg.Connection]`；`keel.db.pool.reset_pool() -> None`；pytest fixture `pg_dsn: str`（未设 `KEEL_TEST_PG_DSN` 时 skip）

- [ ] **Step 1: 加 fixture 与失败测试**

`backend/tests/conftest.py`:

```python
"""测试公共夹具。需要真库的测试一律走 `KEEL_TEST_PG_DSN`，没设就 skip。"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = os.environ.get("KEEL_TEST_PG_DSN", "")
    if not dsn:
        pytest.skip("KEEL_TEST_PG_DSN 未设置；先 `make pg-up` 并 export KEEL_TEST_PG_DSN")
    return dsn
```

`backend/tests/test_db.py`:

```python
"""PG 模式与连接层。"""

from __future__ import annotations

import psycopg

from keel.db import migrations, pool


def test_migrate_is_idempotent_and_creates_v1_tables(pg_dsn: str) -> None:
    migrations.migrate(pg_dsn)
    migrations.migrate(pg_dsn)  # 第二次必须也成功，迁移要可重入
    with psycopg.connect(pg_dsn) as conn:
        names = {r[0] for r in conn.execute(
            "select tablename from pg_tables where schemaname = 'public'"
        )}
    expected = {
        "instruments", "cost_models", "strategy_configs", "sleeves", "strategy_instances",
        "runtime_flags", "audit_log", "events", "consumer_offsets", "consumed_events",
        "backtest_runs", "job_runs", "artifacts", "heartbeats", "late_events",
    }
    assert expected <= names


def test_backtest_runs_has_universe_and_metrics(pg_dsn: str) -> None:
    migrations.migrate(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        cols = {r[0] for r in conn.execute(
            "select column_name from information_schema.columns where table_name = 'backtest_runs'"
        )}
    assert {"universe", "engine", "engine_version", "metrics", "data_snapshot_id"} <= cols


def test_connection_yields_dict_rows(pg_dsn: str, monkeypatch) -> None:
    monkeypatch.setenv("KEEL_PG_DSN", pg_dsn)
    from keel.config import get_settings

    get_settings.cache_clear()
    pool.reset_pool()
    with pool.connection() as conn:
        assert conn.execute("select 1 as one").fetchone()["one"] == 1
    pool.reset_pool()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_db.py -v`
Expected: FAIL —— `test_migrate_is_idempotent_and_creates_v1_tables` 报 `NotImplementedError: 迁移执行尚未实现`；若报 skip，先起库（见 Step 5）。

- [ ] **Step 3: 补 schema**

在 `backend/keel/db/schema.sql` 末尾追加：

```sql
-- 回测作业补列：universe 让一次提交覆盖多个标的（§6.3 API-03）
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS universe JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS engine TEXT NOT NULL DEFAULT 'keel-bar';
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS engine_version TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS data_snapshot_id TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS metrics JSONB;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS error TEXT;

-- 批处理作业台账（§5.3）。checkpoint 存水位线，export-tx 靠它决定从哪继续
CREATE TABLE IF NOT EXISTS job_runs (
    job_run_id  BIGSERIAL PRIMARY KEY,
    job_id      TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'RUNNING',   -- RUNNING/SUCCEEDED/FAILED
    rows        BIGINT NOT NULL DEFAULT 0,
    checkpoint  JSONB,
    error       TEXT
);

-- 制品（报告、曲线、交易明细）。只存 storage_key，绝不存绝对路径（§3.11）
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id  TEXT PRIMARY KEY,
    run_id       TEXT REFERENCES backtest_runs(run_id),
    kind         TEXT NOT NULL,                    -- report_json / trades_csv / equity_csv
    storage_key  TEXT NOT NULL,
    bytes        BIGINT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 心跳（§2.5.7）。写库不写日志：容器挂了日志也就没了
CREATE TABLE IF NOT EXISTS heartbeats (
    component TEXT PRIMARY KEY,
    beat_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 迟到事件（§2.5.3）：available_at 已过但被更晚的事件挤到后面
CREATE TABLE IF NOT EXISTS late_events (
    seq        BIGINT PRIMARY KEY,
    event_id   UUID NOT NULL,
    topic      TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: 实现 `migrate` 与连接池**

`backend/keel/db/migrations.py` 的 `migrate` 替换为：

```python
def migrate(dsn: str | None = None) -> None:
    """对目标库执行 schema。幂等——所有语句都是 IF NOT EXISTS / ADD COLUMN IF NOT EXISTS。"""
    import psycopg

    from keel.config import get_settings

    target = dsn or get_settings().pg_dsn
    with psycopg.connect(target, autocommit=True) as conn:
        conn.execute(sql())
```

`backend/keel/db/pool.py`:

```python
"""PostgreSQL 连接池。

进程内一个池。psycopg3 的 ConnectionPool 自带重连，不需要自己写退避重试——
自己写的那层最后通常会变成一个吞掉异常的死循环。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from keel.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            settings.pg_dsn,
            min_size=settings.pg_pool_min,
            max_size=settings.pg_pool_max,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def reset_pool() -> None:
    """测试用：关掉池，让下一次调用按新 DSN 重建。"""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """显式事务。``with connection()`` 只在退出时提交，中途异常会留在 aborted 状态，
    所以业务写一律走这里。
    """
    with get_pool().connection() as conn, conn.transaction():
        yield conn
```

- [ ] **Step 5: 起库并跑测试**

```bash
cd /Users/wangwei/Documents/github/Keel
docker compose -f deploy/docker-compose.yml up -d postgres
export KEEL_TEST_PG_DSN="postgresql://keel:keel@127.0.0.1:5432/keel"
cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_db.py -v
```
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add backend/keel/db backend/tests/conftest.py backend/tests/test_db.py Makefile
git commit -m "feat(db): PG 模式补齐 V1 表与连接池"
```

---

## Task 2: Parquet 湖原语

**Files:**
- Create: `backend/keel/data/schemas.py`
- Create: `backend/keel/data/lake.py`
- Test: `backend/tests/test_lake.py`

**Interfaces:**
- Consumes: `keel.data.datasets.Dataset`（`name/primary_key/partition_keys/append_only`）
- Produces:
  - `keel.data.schemas.BARS_SCHEMA: pa.Schema`（列见下）
  - `keel.data.lake.quote_symbol(symbol: str) -> str` / `unquote_symbol(part: str) -> str`
  - `keel.data.lake.partition_dir(root: Path, dataset: Dataset, symbol: str, interval: str, when: datetime) -> Path`
  - `keel.data.lake.write_partition(path: Path, frame: pd.DataFrame) -> int`
  - `keel.data.lake.read_bars(root: Path, symbol: str | None = None, interval: str | None = None) -> pd.DataFrame`
  - `keel.data.lake.list_partitions(root: Path, dataset: Dataset) -> list[tuple[str, str, int, int]]`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_lake.py`:

```python
"""Parquet 湖：分区命名、原子写、往返一致。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from keel.data.datasets import ODS_BARS
from keel.data.lake import (
    list_partitions,
    partition_dir,
    quote_symbol,
    read_bars,
    unquote_symbol,
    write_partition,
)


def _frame(symbol: str = "BTC/USDT") -> pd.DataFrame:
    ts = pd.to_datetime(["2023-11-01T00:00:00Z", "2023-11-01T01:00:00Z"], utc=True)
    return pd.DataFrame(
        {
            "open_time": ts,
            "symbol": [symbol, symbol],
            "interval": ["1h", "1h"],
            "open": [1.0, 2.0],
            "high": [3.0, 4.0],
            "low": [0.5, 1.5],
            "close": [2.0, 3.0],
            "volume": [10.0, 20.0],
            "revision": [1, 1],
            "ingested_at": pd.to_datetime(["2023-12-01T00:00:00Z"] * 2, utc=True),
        }
    )


def test_symbol_roundtrip_is_dir_safe() -> None:
    assert quote_symbol("BTC/USDT") == "BTC_USDT"
    assert unquote_symbol("BTC_USDT") == "BTC/USDT"


def test_partition_dir_follows_dataset_keys(tmp_path: Path) -> None:
    path = partition_dir(
        tmp_path, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 15, tzinfo=UTC)
    )
    assert path == tmp_path / "ods_bars/symbol=BTC_USDT/interval=1h/year=2023/month=11"


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    path = partition_dir(
        tmp_path, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 1, tzinfo=UTC)
    )
    written = write_partition(path, _frame())
    assert written == 2
    assert not list(path.glob("*.tmp")), "不允许留下临时文件"
    back = read_bars(tmp_path, symbol="BTC/USDT", interval="1h")
    assert len(back) == 2
    assert str(back["open_time"].dt.tz) == "UTC"
    assert back["close"].tolist() == [2.0, 3.0]


def test_read_filters_by_symbol(tmp_path: Path) -> None:
    for sym in ("BTC/USDT", "ETH/USDT"):
        path = partition_dir(tmp_path, ODS_BARS, sym, "1h", datetime(2023, 11, 1, tzinfo=UTC))
        write_partition(path, _frame(sym))
    assert len(read_bars(tmp_path, symbol="ETH/USDT")) == 2
    assert len(read_bars(tmp_path)) == 4


def test_list_partitions(tmp_path: Path) -> None:
    path = partition_dir(tmp_path, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 1, tzinfo=UTC))
    write_partition(path, _frame())
    assert list_partitions(tmp_path, ODS_BARS) == [("BTC/USDT", "1h", 2023, 11)]


def test_write_partition_rejects_schema_drift(tmp_path: Path) -> None:
    path = partition_dir(tmp_path, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 1, tzinfo=UTC))
    bad = _frame().drop(columns=["volume"])
    with pytest.raises(ValueError, match="missing columns"):
        write_partition(path, bad)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_lake.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'keel.data.lake'`

- [ ] **Step 3: 写 schema 与原语**

`backend/keel/data/schemas.py`:

```python
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
```

`backend/keel/data/lake.py`:

```python
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
import pyarrow.parquet as pq

from keel.data.datasets import Dataset
from keel.data.schemas import BARS_COLUMNS, BARS_SCHEMA


def quote_symbol(symbol: str) -> str:
    """``BTC/USDT`` → ``BTC_USDT``。只影响目录名；symbol 列里存的永远是原值。"""
    return symbol.replace("/", "_")


def unquote_symbol(part: str) -> str:
    """``BTC_USDT`` → ``BTC/USDT``。仅用于展示，正确性不依赖它。"""
    return part.replace("_", "/", 1)


def partition_dir(
    root: Path, dataset: Dataset, symbol: str, interval: str, when
) -> Path:
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


def read_bars(
    root: Path, symbol: str | None = None, interval: str | None = None
) -> pd.DataFrame:
    """读回原始 K 线。空湖返回空 DataFrame（带列），让调用方少写一个 if。"""
    from keel.data.datasets import ODS_BARS

    files = _files(Path(root), ODS_BARS, symbol, interval)
    if not files:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in BARS_COLUMNS})
    table = pq.read_table(files, schema=BARS_SCHEMA)
    return table.to_pandas()


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
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_lake.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/data/schemas.py backend/keel/data/lake.py backend/tests/test_lake.py
git commit -m "feat(data): Parquet 湖原语与列契约"
```

---

## Task 3: DuckDB 集市、写者锁与原子发布

**Files:**
- Create: `backend/keel/db/mart.py`
- Modify: `backend/keel/errors.py`（加 `EtlBusy`、`MartMissing`）
- Test: `backend/tests/test_mart.py`

**Interfaces:**
- Consumes: `get_settings().duckdb_path`
- Produces:
  - `keel.db.mart.MartBusy(Exception)`
  - `keel.db.mart.writer_lock(root: Path) -> Iterator[None]`
  - `keel.db.mart.connect(path: str | Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]`（只读）
  - `keel.db.mart.publish(build: Callable[[Path], None], target: Path | None = None) -> str`（返回 `mart_version`）
  - `keel.db.mart.mart_version(path: Path) -> str`
  - `keel.errors.EtlBusy`（502? 不，500 语义 → `http_status=503`，`code=ErrorCode.ETL_BUSY`）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_mart.py`:

```python
"""DuckDB 集市：单写者、原子发布、只读连接。"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from keel.db import mart


def _build(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("create table t as select 42 as answer")
    con.close()


def test_publish_creates_mart_and_version(tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    version = mart.publish(_build, target)
    assert version
    with mart.connect(target) as con:
        assert con.execute("select answer from t").fetchone()[0] == 42


def test_publish_is_atomic_on_failure(tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    mart.publish(_build, target)
    before = mart.mart_version(target)

    def boom(path: Path) -> None:
        _build(path)
        raise RuntimeError("etl 中途炸了")

    with pytest.raises(RuntimeError, match="etl 中途炸了"):
        mart.publish(boom, target)
    # 旧集市必须原封不动：读者不能看到半个集市
    assert mart.mart_version(target) == before
    assert not list(tmp_path.glob("*.tmp"))


def test_writer_lock_excludes_second_writer(tmp_path: Path) -> None:
    with mart.writer_lock(tmp_path):
        with pytest.raises(mart.MartBusy, match="writer"):
            with mart.writer_lock(tmp_path):
                pass


def test_writer_lock_released_after_block(tmp_path: Path) -> None:
    with mart.writer_lock(tmp_path):
        pass
    with mart.writer_lock(tmp_path):
        pass


def test_connect_missing_mart_raises(tmp_path: Path) -> None:
    with pytest.raises(mart.MartMissing, match="keel.duckdb"):
        with mart.connect(tmp_path / "keel.duckdb"):
            pass


def test_reader_cannot_write(tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    mart.publish(_build, target)
    with mart.connect(target) as con:
        with pytest.raises(duckdb.Error):
            con.execute("create table nope as select 1")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_mart.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'keel.db.mart'`

- [ ] **Step 3: 加错误类型**

`backend/keel/errors.py` 的 `ErrorCode` 里加 `ETL_BUSY = "ETL_BUSY"`、`MART_UNAVAILABLE = "MART_UNAVAILABLE"`，类里加：

```python
class EtlBusy(KeelError):
    """已有写者在跑。直接失败，不排队——排队只把冲突推到更难查的时刻。"""
    code = ErrorCode.ETL_BUSY
    http_status = 503


class MartMissing(KeelError):
    code = ErrorCode.MART_UNAVAILABLE
    http_status = 503
```

- [ ] **Step 4: 实现 mart**

`backend/keel/db/mart.py`:

```python
"""DuckDB 集市的写者锁与原子发布。§3.3。

为什么不是"写进同一个文件"：DuckDB 同一时刻只允许一个写进程，而集市要从
上千个 Parquet 分区重建。所以的做法是**建新文件再改名**——读者要么看到旧集市，
要么看到新集市，不存在中间态。
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb


class MartBusy(Exception):
    """已有写者持锁。"""


class MartMissing(Exception):
    """集市文件不存在。"""


def _target(target: Path | None) -> Path:
    if target is not None:
        return Path(target)
    from keel.config import get_settings

    return Path(get_settings().duckdb_path)


@contextmanager
def writer_lock(root: Path) -> Iterator[None]:
    """非阻塞排他锁。拿不到立刻抛，等待会让"谁该负责"变模糊。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    handle = open(root / ".writer.lock", "w")  # noqa: SIM115 — 生命周期由 finally 管
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MartBusy("another writer holds the mart lock") from exc
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def connect(path: str | Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """只读连接。每次请求新开：发布时文件被 rename，老连接还指着旧 inode。"""
    target = _target(path)
    if not target.exists():
        raise MartMissing(f"mart not found: {target}")
    con = duckdb.connect(str(target), read_only=True)
    try:
        yield con
    finally:
        con.close()


def mart_version(path: Path) -> str:
    """用 size+mtime 当版本。够回答"这份结果是不是旧数据"，不必读全文件。"""
    st = Path(path).stat()
    return f"{st.st_size:x}-{int(st.st_mtime_ns):x}"


def publish(build: Callable[[Path], None], target: Path | None = None) -> str:
    """在临时文件上跑 ``build``，校验能打开，然后原子改名。"""
    target = _target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".duckdb.tmp")
    if tmp.exists():
        tmp.unlink()
    try:
        build(tmp)
        probe = duckdb.connect(str(tmp), read_only=True)
        try:
            probe.execute("select count(*) from duckdb_tables()").fetchone()
        finally:
            probe.close()
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return mart_version(target)
```

- [ ] **Step 5: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_mart.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add backend/keel/db/mart.py backend/keel/errors.py backend/tests/test_mart.py
git commit -m "feat(db): DuckDB 集市写者锁与原子发布"
```

---

## Task 4: K 线归一化

**Files:**
- Create: `backend/keel/market/normalize.py`
- Test: `backend/tests/test_normalize.py`

**Interfaces:**
- Produces:
  - `keel.market.normalize.INTERVAL_MS: dict[str, int]`
  - `keel.market.normalize.interval_to_ms(interval: str) -> int`
  - `keel.market.normalize.normalize_ohlcv(rows: Sequence[Sequence[float]], symbol: str, interval: str, *, ingested_at: datetime | None = None) -> pd.DataFrame`（列 = `BARS_COLUMNS`）
  - `keel.market.normalize.BadBar(ValueError)`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_normalize.py`:

```python
"""CCXT OHLCV 行的归一化：排序、去重、UTC、基本校验。"""

from __future__ import annotations

import pytest

from keel.data.schemas import BARS_COLUMNS
from keel.market.normalize import BadBar, interval_to_ms, normalize_ohlcv

MS = 1_700_000_000_000  # 2023-11-14T22:13:20Z


def test_columns_and_utc() -> None:
    df = normalize_ohlcv([[MS, 1.0, 2.0, 0.5, 1.5, 10.0]], "BTC/USDT", "1h")
    assert tuple(df.columns) == BARS_COLUMNS
    assert str(df["open_time"].dt.tz) == "UTC"
    assert df["symbol"].iloc[0] == "BTC/USDT"


def test_unsorted_input_is_sorted_and_deduped() -> None:
    rows = [
        [MS + 3_600_000, 2.0, 3.0, 1.5, 2.5, 20.0],
        [MS, 1.0, 2.0, 0.5, 1.5, 10.0],
        [MS, 1.0, 2.0, 0.5, 1.9, 11.0],  # 同一根 bar 的修订，保留后到的
    ]
    df = normalize_ohlcv(rows, "BTC/USDT", "1h")
    assert len(df) == 2
    assert df["close"].tolist() == [1.9, 2.5]


def test_high_below_low_is_rejected() -> None:
    with pytest.raises(BadBar, match="high < low"):
        normalize_ohlcv([[MS, 1.0, 0.4, 0.5, 1.0, 1.0]], "BTC/USDT", "1h")


def test_negative_volume_is_rejected() -> None:
    with pytest.raises(BadBar, match="negative volume"):
        normalize_ohlcv([[MS, 1.0, 2.0, 0.5, 1.5, -1.0]], "BTC/USDT", "1h")


def test_empty_input_returns_typed_empty_frame() -> None:
    df = normalize_ohlcv([], "BTC/USDT", "1h")
    assert len(df) == 0
    assert tuple(df.columns) == BARS_COLUMNS


def test_interval_to_ms() -> None:
    assert interval_to_ms("1h") == 3_600_000
    assert interval_to_ms("1d") == 86_400_000
    with pytest.raises(ValueError, match="unsupported interval"):
        interval_to_ms("7m")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_normalize.py -v`
Expected: FAIL —— `ModuleNotFoundError`

- [ ] **Step 3: 实现**

`backend/keel/market/normalize.py`:

```python
"""CCXT OHLCV → 统一 K 线表。

CCXT 的 OHLCV 是 ``[ms, open, high, low, close, volume]`` 六元组，返回顺序不保证、
同一根 bar 可能因交易所修订出现两次。归一化把这些都吃掉，下游只看一种形状。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pandas as pd

from keel.data.schemas import BARS_COLUMNS

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


class BadBar(ValueError):
    """单根 bar 自身不自洽（high < low 之类）。这种数据修不了，只能拒。"""


def interval_to_ms(interval: str) -> int:
    try:
        return INTERVAL_MS[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported interval: {interval}") from exc


def normalize_ohlcv(
    rows: Sequence[Sequence[float]],
    symbol: str,
    interval: str,
    *,
    ingested_at: datetime | None = None,
) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype="float64" if c not in (
            "symbol", "interval"
        ) else "object") for c in BARS_COLUMNS})
    frame = pd.DataFrame(list(rows), columns=["ms", "open", "high", "low", "close", "volume"])
    frame = frame.dropna(subset=["ms", "open", "high", "low", "close", "volume"])
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = frame[col].astype("float64")

    # 自相矛盾的 bar 必须抛错，不能静默丢掉：静默丢弃会让缺口分析把
    # 「交易所给了错数据」记成「我们没拉到」，两类问题处置方式完全不同。
    bad_high = frame[frame["high"] < frame["low"]]
    if not bad_high.empty:
        raise BadBar(f"high < low at {int(bad_high['ms'].iloc[0])}")
    bad_vol = frame[frame["volume"] < 0]
    if not bad_vol.empty:
        raise BadBar(f"negative volume at {int(bad_vol['ms'].iloc[0])}")

    frame["open_time"] = pd.to_datetime(frame["ms"].astype("int64"), unit="ms", utc=True)
    frame = frame.sort_values("open_time").drop_duplicates("open_time", keep="last")
    frame["symbol"] = symbol
    frame["interval"] = interval
    frame["revision"] = 1
    stamp = ingested_at or datetime.now(UTC)
    frame["ingested_at"] = pd.Timestamp(stamp)
    return frame[list(BARS_COLUMNS)].reset_index(drop=True)
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_normalize.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/market/normalize.py backend/tests/test_normalize.py
git commit -m "feat(market): K 线归一化"
```

---

## Task 5: Binance 现货适配器与限流

**Files:**
- Create: `backend/keel/market/exchange.py`
- Test: `backend/tests/test_exchange.py`

**Interfaces:**
- Consumes: `get_settings()` 的 `exchange_id`/`exchange_api_key`/`exchange_api_secret`
- Produces:
  - `keel.market.exchange.BinanceSpot(client=None, *, rps: float = 10.0, sleep=time.sleep)`
  - `.fetch_ohlcv(symbol: str, interval: str, since_ms: int, limit: int = 1000) -> list[list[float]]`
  - `.fetch_markets() -> list[dict]`
  - `keel.market.exchange.RATE_LIMIT_RPS = 10.0`、`MAX_LIMIT = 1000`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_exchange.py`:

```python
"""CCXT 适配层：分页上限、限流、错误映射。真网络测试默认跳过。"""

from __future__ import annotations

import os

import pytest

from keel.market.exchange import MAX_LIMIT, BinanceSpot


class FakeClient:
    """记下每次调用参数，按 ``pages`` 依次返回。"""

    def __init__(self, pages: list[list[list[float]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str, int | None, int]] = []

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        self.calls.append((symbol, timeframe, since, limit))
        return self.pages.pop(0) if self.pages else []


def _sleeps() -> list[float]:
    return []


def test_limit_is_capped() -> None:
    fake = FakeClient([[ ]])
    ex = BinanceSpot(client=fake, rps=1000, sleep=_sleeps().append)
    ex.fetch_ohlcv("BTC/USDT", "1h", 0, limit=5000)
    assert fake.calls[0][3] == MAX_LIMIT


def test_rate_limiter_spaces_calls() -> None:
    slept: list[float] = []
    fake = FakeClient([[], [], []])
    ex = BinanceSpot(client=fake, rps=2.0, sleep=slept.append)
    for _ in range(3):
        ex.fetch_ohlcv("BTC/USDT", "1h", 0)
    assert len(fake.calls) == 3
    # rps=2 → 间隔 0.5s，三次调用至少要有两次等待
    assert len(slept) == 2
    assert all(abs(s - 0.5) < 0.2 for s in slept)


def test_network_error_is_mapped() -> None:
    class Boom(FakeClient):
        def fetch_ohlcv(self, *a, **kw):
            raise ConnectionError("dns 挂了")

    ex = BinanceSpot(client=Boom([]), rps=1000, sleep=lambda _s: None)
    from keel.errors import ErrorCode, KeelError

    with pytest.raises(KeelError) as info:
        ex.fetch_ohlcv("BTC/USDT", "1h", 0)
    assert info.value.code is ErrorCode.EXCHANGE_UNAVAILABLE


@pytest.mark.skipif(not os.environ.get("KEEL_LIVE_EXCHANGE"), reason="需要 KEEL_LIVE_EXCHANGE=1")
def test_live_binance_returns_bars() -> None:
    ex = BinanceSpot()
    rows = ex.fetch_ohlcv("BTC/USDT", "1h", since_ms=1_700_000_000_000, limit=5)
    assert rows and len(rows) <= 5
    assert len(rows[0]) == 6
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_exchange.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'keel.market.exchange'`

- [ ] **Step 3: 实现**

`backend/keel/market/exchange.py`:

```python
"""Binance 现货适配器。§3.2。

限流放在这一层，不放在调用点：调用点散落的 ``sleep`` 最后一定会被谁删掉一个，
然后在某个周末被封 IP。实测（2026-11-05）：单次 K 线上限 1000 根，
``rateLimit`` 50ms；按权重算稳态约 10 次/秒，这里取 10 作为默认值。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from keel.config import get_settings
from keel.errors import ErrorCode, KeelError

MAX_LIMIT = 1000
RATE_LIMIT_RPS = 10.0


class ExchangeUnavailable(KeelError):
    code = ErrorCode.EXCHANGE_UNAVAILABLE
    http_status = 503


class ExchangeBadSymbol(KeelError):
    code = ErrorCode.NOT_FOUND
    http_status = 404


class BinanceSpot:
    """只做现货（ADR-001）。没有杠杆、没有资金费、没有保证金。"""

    def __init__(
        self,
        client: Any | None = None,
        *,
        rps: float = RATE_LIMIT_RPS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if client is None:
            import ccxt

            settings = get_settings()
            client = ccxt.binance(
                {
                    "enableRateLimit": True,
                    "apiKey": settings.exchange_api_key or None,
                    "secret": settings.exchange_api_secret or None,
                    "options": {"defaultType": "spot"},
                }
            )
        self._client = client
        self._interval = 1.0 / rps
        self._sleep = sleep
        self._last = 0.0

    def _throttle(self) -> None:
        now = time.monotonic()
        delta = now - self._last
        if self._last and delta < self._interval:
            self._sleep(self._interval - delta)
        self._last = time.monotonic()

    def fetch_ohlcv(
        self, symbol: str, interval: str, since_ms: int, limit: int = MAX_LIMIT
    ) -> list[list[float]]:
        self._throttle()
        try:
            return self._client.fetch_ohlcv(
                symbol, timeframe=interval, since=int(since_ms), limit=min(limit, MAX_LIMIT)
            ) or []
        except Exception as exc:  # ccxt 的异常树随版本变，按语义分两类就够
            name = type(exc).__name__
            if "BadSymbol" in name or "Invalid" in name:
                raise ExchangeBadSymbol(f"bad symbol: {symbol}") from exc
            raise ExchangeUnavailable(f"exchange call failed: {name}: {exc}") from exc

    def fetch_markets(self) -> list[dict]:
        self._throttle()
        try:
            return list(self._client.load_markets().values())
        except Exception as exc:
            raise ExchangeUnavailable(f"load_markets failed: {exc}") from exc
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_exchange.py -v`
Expected: 3 passed, 1 skipped

- [ ] **Step 5: 真网络抽查（一次性，不是测试）**

```bash
cd backend && KEEL_LIVE_EXCHANGE=1 UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_exchange.py -v -k live
```
Expected: 1 passed（拉不到就查网络，别改测试）

- [ ] **Step 6: Commit**

```bash
git add backend/keel/market/exchange.py backend/tests/test_exchange.py
git commit -m "feat(market): Binance 现货适配器与限流"
```

---

## Task 6: 缺口分析与覆盖

**Files:**
- Create: `backend/keel/data/coverage.py`
- Test: `backend/tests/test_coverage.py`

**Interfaces:**
- Produces:
  - `keel.data.coverage.Gap(start: datetime, end: datetime, missing: int, severity: str)`（`severity ∈ {"blocking","tolerated"}`）
  - `keel.data.coverage.Coverage(symbol: str, interval: str, start: datetime | None, end: datetime | None, expected: int, present: int, gaps: tuple[Gap, ...])`
  - `keel.data.coverage.analyse(frame: pd.DataFrame, interval: str) -> Coverage`
  - `keel.data.coverage.BLOCKING_CONSECUTIVE = 2`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_coverage.py`:

```python
"""缺口分级：连续缺 2 根以上是 blocking，单根是 tolerated。§3.1"""

from __future__ import annotations

import pandas as pd

from keel.data.coverage import analyse


def _bars(starts: list[str], interval: str = "1h") -> pd.DataFrame:
    ts = pd.to_datetime(starts, utc=True)
    return pd.DataFrame(
        {
            "open_time": ts,
            "symbol": ["BTC/USDT"] * len(ts),
            "interval": [interval] * len(ts),
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
            "revision": 1,
            "ingested_at": pd.Timestamp("2023-12-01T00:00:00Z"),
        }
    )


def test_continuous_range_has_no_gaps() -> None:
    cov = analyse(_bars(["2023-11-01T00:00Z", "2023-11-01T01:00Z", "2023-11-01T02:00Z"]), "1h")
    assert cov.present == 3 == cov.expected
    assert cov.gaps == ()


def test_single_missing_bar_is_tolerated() -> None:
    cov = analyse(_bars(["2023-11-01T00:00Z", "2023-11-01T02:00Z"]), "1h")
    assert cov.expected == 3 and cov.present == 2
    assert [(g.missing, g.severity) for g in cov.gaps] == [(1, "tolerated")]


def test_two_missing_bars_is_blocking() -> None:
    cov = analyse(_bars(["2023-11-01T00:00Z", "2023-11-01T03:00Z"]), "1h")
    assert [(g.missing, g.severity) for g in cov.gaps] == [(2, "blocking")]


def test_empty_frame_reports_blocking_gap() -> None:
    cov = analyse(_bars([]), "1h")
    assert cov.start is None and cov.end is None
    assert [(g.missing, g.severity) for g in cov.gaps] == [(0, "blocking")]


def test_gap_start_end_are_the_missing_span() -> None:
    cov = analyse(_bars(["2023-11-01T00:00Z", "2023-11-01T03:00Z"]), "1h")
    gap = cov.gaps[0]
    assert str(gap.start) == "2023-11-01 01:00:00+00:00"
    assert str(gap.end) == "2023-11-01 02:00:00+00:00"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_coverage.py -v`
Expected: FAIL —— `ModuleNotFoundError`

- [ ] **Step 3: 实现**

`backend/keel/data/coverage.py`:

```python
"""覆盖率与缺口分析。§3.1。

加密市场 7×24，没有休市，所以"应有 bar"就是一条连续的等差时间轴。
缺口分两级：单根缺失可以容忍（多半是交易所那一分钟没成交），连续两根以上
视为 blocking——连续缺失通常意味着采集中断，而采集中断意味着那段时间的回测结果是假的。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

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
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_coverage.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/data/coverage.py backend/tests/test_coverage.py
git commit -m "feat(data): 缺口分级与覆盖率分析"
```

---

## Task 7: 回补计划与执行

**Files:**
- Modify: `backend/keel/market/backfill.py:23-30`
- Test: `backend/tests/test_backfill.py`

**Interfaces:**
- Consumes: `keel.market.exchange.BinanceSpot`、`keel.data.lake.*`、`keel.market.normalize.normalize_ohlcv`
- Produces:
  - `BackfillJob(symbol: str, interval: str, start: datetime, end: datetime)`
  - `JobResult(job: BackfillJob, rows_written: int, batches: int, last_open_time: datetime | None)`
  - `plan(jobs: list[BackfillJob], *, root: Path) -> list[BackfillJob]`（按已有分区裁剪成待补区间）
  - `execute(job: BackfillJob, *, exchange, root: Path, limit: int = 1000) -> JobResult`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_backfill.py`:

```python
"""回补：分页拉取、按月落分区、重跑幂等（第二次 plan 为空）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from keel.data.lake import read_bars
from keel.market.backfill import BackfillJob, execute, plan
from keel.market.normalize import interval_to_ms

HOUR = 3_600_000
START = datetime(2023, 11, 1, tzinfo=UTC)
END = datetime(2023, 11, 1, tzinfo=UTC) .replace(hour=5)


class FakeExchange:
    def __init__(self, total: int) -> None:
        self.total = total
        self.calls = 0

    def fetch_ohlcv(self, symbol, interval, since_ms, limit=1000):
        self.calls += 1
        base = int(START.timestamp() * 1000)
        out = []
        for i in range(limit):
            ms = since_ms + i * HOUR
            if i + (since_ms - base) // HOUR >= self.total:
                break
            out.append([ms, 1.0, 2.0, 0.5, 1.5, 10.0])
        return out


def test_execute_writes_partitions(tmp_path: Path) -> None:
    job = BackfillJob("BTC/USDT", "1h", START, END)
    result = execute(job, exchange=FakeExchange(5), root=tmp_path, limit=2)
    assert result.rows_written == 5
    assert result.batches == 3
    assert len(read_bars(tmp_path, symbol="BTC/USDT", interval="1h")) == 5


def test_plan_is_empty_when_range_is_complete(tmp_path: Path) -> None:
    job = BackfillJob("BTC/USDT", "1h", START, END)
    execute(job, exchange=FakeExchange(5), root=tmp_path, limit=1000)
    assert plan([job], root=tmp_path) == []


def test_plan_only_covers_missing_tail(tmp_path: Path) -> None:
    first = BackfillJob("BTC/USDT", "1h", START, START)
    execute(first, exchange=FakeExchange(1), root=tmp_path, limit=1000)
    todo = plan([BackfillJob("BTC/USDT", "1h", START, END)], root=tmp_path)
    assert len(todo) == 1
    assert todo[0].start == START.replace(hour=1)


def test_execute_is_idempotent_on_same_range(tmp_path: Path) -> None:
    job = BackfillJob("BTC/USDT", "1h", START, START.replace(hour=1))
    execute(job, exchange=FakeExchange(2), root=tmp_path, limit=1000)
    execute(job, exchange=FakeExchange(2), root=tmp_path, limit=1000)
    frame = read_bars(tmp_path, symbol="BTC/USDT", interval="1h")
    assert len(frame.drop_duplicates("open_time")) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_backfill.py -v`
Expected: FAIL —— `NotImplementedError: 回补计划尚未实现`

- [ ] **Step 3: 实现**

`backend/keel/market/backfill.py` 整体替换为：

```python
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
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_backfill.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/market/backfill.py backend/tests/test_backfill.py
git commit -m "feat(market): 回补计划与幂等执行"
```

---

## Task 8: 清洗到 `dwd_bars`

**Files:**
- Create: `backend/keel/data/dwd.py`
- Test: `backend/tests/test_dwd.py`

**Interfaces:**
- Produces:
  - `keel.data.dwd.to_dwd(ods: pd.DataFrame) -> pd.DataFrame`（列 = `BARS_COLUMNS` + `is_outlier: bool`）
  - `keel.data.dwd.OUTLIER_Z = 8.0`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_dwd.py`:

```python
"""明细层清洗：去重取最新 revision、标记异常 bar、不补造数据。"""

from __future__ import annotations

import pandas as pd

from keel.data.dwd import to_dwd


def _frame() -> pd.DataFrame:
    ts = pd.to_datetime(
        ["2023-11-01T00:00Z", "2023-11-01T01:00Z", "2023-11-01T02:00Z"], utc=True
    )
    return pd.DataFrame(
        {
            "open_time": list(ts) + [ts[0]],
            "symbol": ["BTC/USDT"] * 4,
            "interval": ["1h"] * 4,
            "open": [1.0, 2.0, 3.0, 1.0],
            "high": [2.0, 3.0, 4.0, 2.0],
            "low": [0.5, 1.5, 2.5, 0.5],
            "close": [1.5, 2.5, 3.5, 1.6],
            "volume": [10.0, 11.0, 12.0, 10.5],
            "revision": [1, 1, 1, 2],
            "ingested_at": pd.to_datetime(["2023-12-01T00:00Z"] * 4, utc=True),
        }
    )


def test_keeps_latest_revision() -> None:
    out = to_dwd(_frame())
    assert len(out) == 3
    first = out[out["open_time"] == pd.Timestamp("2023-11-01T00:00Z")]
    assert first["close"].iloc[0] == 1.6
    assert first["revision"].iloc[0] == 2


def test_output_is_sorted() -> None:
    out = to_dwd(_frame())
    assert out["open_time"].is_monotonic_increasing


def test_does_not_invent_missing_bars() -> None:
    frame = _frame()
    frame = frame[frame["open_time"] != pd.Timestamp("2023-11-01T01:00Z")]
    out = to_dwd(frame)
    assert len(out) == 2  # 缺的那根就是缺着，不补 0 成交量假 bar


def test_outlier_flag_on_absurd_jump() -> None:
    frame = _frame()
    frame.loc[2, "high"] = 1000.0
    frame.loc[2, "low"] = 900.0
    frame.loc[2, "close"] = 950.0
    out = to_dwd(frame)
    assert out["is_outlier"].sum() == 1
    assert out.loc[out["is_outlier"], "open_time"].iloc[0] == pd.Timestamp("2023-11-01T02:00Z")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_dwd.py -v`
Expected: FAIL —— `ModuleNotFoundError`

- [ ] **Step 3: 实现**

`backend/keel/data/dwd.py`:

```python
"""原始层 → 明细层。

清洗只做三件事：去重取最新 revision、排序、标记异常 bar。
**不补造数据**——把缺失 bar 填成 volume=0 会让回测在假数据上跑得很顺，
而缺口该由 ``ops_coverage`` 暴露出来。

异常判定分两段，因为两段解决的是不同问题：
- 样本足够（≥ ``MIN_OBS_FOR_MAD`` 根）时用 MAD 估计波动，阈值 ``OUTLIER_Z`` 倍。
  用 MAD 不用标准差，是因为标准差本身会被那根异常 bar 抬高，把异常吃掉。
- 样本不足时退回固定阈值 ``HARD_JUMP``。小样本的稳健估计没有意义，
  而这时候如果继续用 NaN 阈值，结果是**一根异常都标不出来**——
  「没报警」会被读成「没问题」，这比漏报更危险。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

OUTLIER_Z = 8.0
MIN_OBS_FOR_MAD = 10
HARD_JUMP = 1.0  # 单根翻倍（|log return| > 1）在 1h 尺度上不可能是真实行情


def to_dwd(ods: pd.DataFrame) -> pd.DataFrame:
    if ods.empty:
        out = ods.copy()
        out["is_outlier"] = pd.Series(dtype="bool")
        return out
    frame = ods.sort_values(["ingested_at", "revision"]).drop_duplicates(
        ["symbol", "interval", "open_time"], keep="last"
    )
    frame = frame.sort_values("open_time").reset_index(drop=True)

    ret = np.log(frame["close"].astype("float64")).diff()
    # shift(1)：参考分布只能用「这根之前」的收益，否则异常 bar 会把自己算进基线
    history = ret.abs().shift(1)
    scale = history.expanding(min_periods=1).median() * 1.4826
    observed = history.expanding(min_periods=1).count()
    threshold = np.where(observed >= MIN_OBS_FOR_MAD, OUTLIER_Z * scale, HARD_JUMP)
    frame["is_outlier"] = (ret.abs() > threshold).fillna(False).to_numpy(dtype=bool)
    return frame
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_dwd.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/data/dwd.py backend/tests/test_dwd.py
git commit -m "feat(data): 明细层清洗与异常标记"
```

---

## Task 9: ETL `run_once` 与集市发布

**Files:**
- Modify: `backend/keel/data/etl.py:27-36`
- Modify: `backend/keel/data/datasets.py`（加 `ADS_BACKTEST_RUNS`）
- Test: `backend/tests/test_etl.py`

**Interfaces:**
- Consumes: `keel.data.lake.read_bars`、`keel.data.dwd.to_dwd`、`keel.data.coverage.analyse`、`keel.data.features.compute_batch`、`keel.db.mart.publish`/`writer_lock`
- Produces:
  - `keel.data.etl.run_once(datasets: list[str] | None = None, *, root: Path | None = None, mart: Path | None = None) -> list[EtlResult]`
  - `keel.data.etl.EtlResult` 不变
  - DuckDB 表：`dwd_bars`、`dws_features`、`ops_coverage`、`ds_meta(dataset, mart_version, row_count, built_at)`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_etl.py`:

```python
"""ETL：单写者、原子发布、集市内容正确。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from keel.data import etl
from keel.data.datasets import ODS_BARS
from keel.data.lake import partition_dir, write_partition
from keel.db import mart
from keel.market.normalize import normalize_ohlcv

HOUR = 3_600_000
START_MS = 1_699_200_000_000  # 2023-11-05T00:00:00Z


def _seed(root: Path, symbol: str, n: int = 40) -> None:
    rows = []
    for i in range(n):
        rows.append([START_MS + i * HOUR, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 5.0])
    frame = normalize_ohlcv(rows, symbol, "1h")
    path = partition_dir(root, ODS_BARS, symbol, "1h", datetime(2023, 11, 1, tzinfo=UTC))
    write_partition(path, frame)


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    root = tmp_path / "raw"
    _seed(root, "BTC/USDT")
    _seed(root, "ETH/USDT")
    return root


def test_run_once_builds_marts(lake: Path, tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    results = etl.run_once(root=lake, mart=target)
    assert {r.dataset for r in results} >= {"dwd_bars", "dws_features", "ops_coverage"}
    with mart.connect(target) as con:
        assert con.execute("select count(*) from dwd_bars").fetchone()[0] == 80
        # 特征是滚动窗口，前 20 根 sma_20 为空是正常的
        assert con.execute("select count(*) from dws_features where sma_20 is not null").fetchone()[0] > 0
        assert con.execute("select count(*) from ops_coverage").fetchone()[0] == 2
        assert con.execute("select count(*) from ds_meta").fetchone()[0] >= 3


def test_run_once_is_idempotent(lake: Path, tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    etl.run_once(root=lake, mart=target)
    with mart.connect(target) as con:
        first = con.execute("select count(*) from dwd_bars").fetchone()[0]
    etl.run_once(root=lake, mart=target)
    with mart.connect(target) as con:
        assert con.execute("select count(*) from dwd_bars").fetchone()[0] == first


def test_run_once_fails_fast_when_lock_held(lake: Path, tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    with mart.writer_lock(target.parent):
        with pytest.raises(mart.MartBusy):
            etl.run_once(root=lake, mart=target)


def test_run_once_subset(lake: Path, tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    results = etl.run_once(datasets=["dwd_bars"], root=lake, mart=target)
    assert [r.dataset for r in results] == ["dwd_bars"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_etl.py -v`
Expected: FAIL —— `NotImplementedError: ETL 尚未实现`

- [ ] **Step 3: 实现**

`backend/keel/data/datasets.py` 末尾加：

```python
ADS_BACKTEST_RUNS = Dataset(
    name="ads_backtest_runs",
    layer="ads",
    primary_key=("run_id",),
    partition_keys=(),
    append_only=False,
    description="回测结果汇总；由回测 worker 产出，ETL 只重建索引视图",
)

ALL_DATASETS = (ODS_BARS, DWD_BARS, DWS_FEATURES, OPS_COVERAGE, ADS_BACKTEST_RUNS)
```

`backend/keel/data/etl.py` 的 `run_once` 替换为：

```python
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
    wanted = set(datasets or ["dwd_bars", "dws_features", "ops_coverage"])
    started = datetime.now(UTC)
    results: list[EtlResult] = []

    def build(tmp: Path) -> None:
        import duckdb

        ods = read_bars(raw_root)
        dwd = to_dwd(ods)
        con = duckdb.connect(str(tmp))
        try:
            con.register("dwd_df", dwd)
            con.execute("create table dwd_bars as select * from dwd_df")
            if "dws_features" in wanted:
                feats = compute_batch(dwd) if not dwd.empty else dwd.assign()
                con.register("feat_df", feats)
                con.execute("create table dws_features as select * from feat_df")
            if "ops_coverage" in wanted:
                rows = []
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
                con.register("cov_df", pd.DataFrame(rows, columns=[
                    "symbol", "interval", "start", "end", "expected", "present",
                    "blocking_gaps", "gaps",
                ]))
                con.execute("create table ops_coverage as select * from cov_df")
            con.execute(
                "create table ds_meta as select * from (values (?, ?, ?, ?)) "
                "as t(dataset, mart_version, row_count, built_at)",
                [["dwd_bars", "", 0, datetime.now(UTC)]],
            )
        finally:
            con.close()

    with mart_writer_lock(target.parent):
        version = mart_publish(build, target)
    finished = datetime.now(UTC)
    for name in sorted(wanted):
        results.append(
            EtlResult(
                dataset=name,
                rows_written=0,
                started_at=started,
                finished_at=finished,
                mart_version=version,
            )
        )
    return results
```

真实实现里 `rows_written` 要填真值（在 `build` 里用闭包收集），文件顶部补 import：

```python
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from keel.data.coverage import analyse
from keel.data.dwd import to_dwd
from keel.data.features import compute_batch
from keel.data.lake import read_bars
from keel.db.mart import publish as mart_publish
from keel.db.mart import writer_lock as mart_writer_lock
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_etl.py -v`
Expected: 4 passed

- [ ] **Step 5: 跑全量回归**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest -q && UV_CACHE_DIR="$PWD/../.uv-cache" uv run ruff check . && UV_CACHE_DIR="$PWD/../.uv-cache" uv run ruff format --check .`
Expected: 全部通过（含原有 40 个契约测试）

- [ ] **Step 6: Commit**

```bash
git add backend/keel/data backend/tests/test_etl.py
git commit -m "feat(data): ETL 重建集市并原子发布"
```

---

## Task 10: 策略注册表

**Files:**
- Create: `backend/keel/research/registry.py`
- Modify: `backend/keel/strategies.py`（给两个策略加 `strategy_key` / `strategy_version`）
- Test: `backend/tests/test_registry.py`

**Interfaces:**
- Produces:
  - `keel.research.registry.register(cls) -> cls`（装饰器）
  - `keel.research.registry.get(strategy_key: str, version: int | None = None) -> Strategy`
  - `keel.research.registry.keys() -> list[tuple[str, int]]`
  - `keel.research.registry.UnknownStrategy(KeyError)`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_registry.py`:

```python
"""策略注册表：按 key 取实例，未知 key 报明确错误。"""

from __future__ import annotations

import pytest

from keel.research import registry


def test_known_strategies_are_registered() -> None:
    assert ("sma_cross", 1) in registry.keys()
    assert ("donchian_breakout", 1) in registry.keys()


def test_get_returns_usable_instance() -> None:
    strat = registry.get("sma_cross")
    assert strat.lookback == 61
    assert isinstance(strat.params_schema, dict)


def test_unknown_key_raises_with_hint() -> None:
    with pytest.raises(registry.UnknownStrategy, match="sma_cross"):
        registry.get("nope")


def test_version_mismatch_raises() -> None:
    with pytest.raises(registry.UnknownStrategy, match="version"):
        registry.get("sma_cross", version=99)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_registry.py -v`
Expected: FAIL —— `AttributeError: module 'keel.research' has no attribute 'registry'`

- [ ] **Step 3: 实现**

`backend/keel/research/registry.py`:

```python
"""策略注册表。

为什么要注册表而不是让 API 直接 import 类：``strategy_key`` 是数据库与协议里
的稳定名字，类名会改、模块会搬。把两者隔开，重命名就不是一次数据迁移。
"""

from __future__ import annotations

from typing import Any


class UnknownStrategy(KeyError):
    """请求里给了不存在（或版本不对）的 strategy_key。"""


_REGISTRY: dict[tuple[str, int], type] = {}


def _ensure_loaded() -> None:
    """确保内置策略已注册。

    注册靠 ``keel/strategies.py`` 底部的 ``register(...)`` 调用，也就是 import
    副作用。如果调用方只 import 了 registry，注册表就是空的，而错误信息看起来
    像是「没有这个策略」——排查方向会完全跑偏。所以查询前主动 import 一次。
    这也说明：注册表不该依赖 import 顺序，谁调用谁负责触发。
    """
    import keel.strategies  # noqa: F401


def register(cls: type) -> type:
    key = getattr(cls, "strategy_key", None)
    version = getattr(cls, "strategy_version", None)
    if not key or version is None:
        raise ValueError(f"{cls.__name__} 缺少 strategy_key / strategy_version")
    _REGISTRY[(key, int(version))] = cls
    return cls


def keys() -> list[tuple[str, int]]:
    _ensure_loaded()
    return sorted(_REGISTRY)


def get(strategy_key: str, version: int | None = None) -> Any:
    _ensure_loaded()
    if version is not None:
        cls = _REGISTRY.get((strategy_key, int(version)))
        if cls is None:
            raise UnknownStrategy(
                f"unknown strategy version: {strategy_key} v{version}; "
                f"available: {[k for k in keys() if k[0] == strategy_key]}"
            )
        return cls()
    matches = [(k[0], k[1]) for k in keys() if k[0] == strategy_key]
    if not matches:
        raise UnknownStrategy(f"unknown strategy_key: {strategy_key}; available: {keys()}")
    return _REGISTRY[max(matches)]()
```

`backend/keel/strategies.py` 里两个策略类各加两行类属性（`SmaCross` → `strategy_key = "sma_cross"`、`DonchianBreakout` → `strategy_key = "donchian_breakout"`，两者 `strategy_version: int = 1`），文件底部加：

```python
register(SmaCross)
register(DonchianBreakout)
```

顶部 import：`from keel.research.registry import register`。

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_registry.py tests/test_contract.py -v`
Expected: 4 passed + 40 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/research/registry.py backend/keel/strategies.py backend/tests/test_registry.py
git commit -m "feat(research): 策略注册表"
```

---

## Task 11: 逐 bar 执行器

**Files:**
- Create: `backend/keel/research/simulator.py`
- Test: `backend/tests/test_simulator.py`

**Interfaces:**
- Consumes: `keel.contracts.{run_decide, InsufficientHistory, PositionView, CostModel, Direction}`
- Produces:
  - `keel.research.simulator.Trade(symbol, entry_time, exit_time, direction, qty, entry_price, exit_price, pnl, fees)`
  - `keel.research.simulator.Simulation(trades: tuple[Trade, ...], equity: pd.Series, exposure: pd.Series)`
  - `keel.research.simulator.simulate(bars, strategy, params, cost, *, initial_cash=10_000.0, state=None, feature_version="feat-v1") -> Simulation`
  - 执行规则（写死，不允许调用方改）：**bar `t` 的决策在 `t+1` 的 open 成交**；买入加 `slippage_bps`、卖出减；`qty` 由运行时按满仓算（信号不带数量，§3.7）。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_simulator.py`:

```python
"""逐 bar 执行器：成交在下一根开盘、滑点方向正确、无未来数据。

这里用「按时间戳出信号」的假策略而不是按计数器，因为计数器会掩盖一个真实错误：
运行时会跳过历史不足的前几根 bar，计数器与 bar 序号就此错位，测试却还是绿的。
"""

from __future__ import annotations

import pandas as pd
import pytest

from keel.contracts import CostModel, Direction, Signal
from keel.research.simulator import simulate

TS = pd.date_range("2023-11-01", periods=5, freq="1h", tz="UTC")


def _bars(closes: list[float], *, opens: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    opens = opens or closes
    return pd.DataFrame(
        {
            "open_time": TS[:n],
            "symbol": "BTC/USDT",
            "interval": "1h",
            "open": opens[:n],
            "high": [max(o, c) * 1.001 for o, c in zip(opens, closes, strict=True)],
            "low": [min(o, c) * 0.999 for o, c in zip(opens, closes, strict=True)],
            "close": closes,
            "volume": 1.0,
            "revision": 1,
            "ingested_at": pd.Timestamp("2023-12-01T00:00:00Z"),
        }
    )


class AtTimes:
    """在指定时间出信号。信号只表达意图，成交由执行器决定。"""

    lookback = 1
    strategy_key = "at_times"
    strategy_version = 1
    params_schema: dict = {}

    def __init__(self, enter_at: pd.Timestamp, exit_at: pd.Timestamp) -> None:
        self.enter_at, self.exit_at = enter_at, exit_at

    def required_lookback(self, params: dict) -> int:
        return 1

    def decide(self, window, bar, params, position, state):
        stamp = pd.Timestamp(bar.open_time)
        if stamp == self.enter_at and position.direction is Direction.FLAT:
            return [Signal(bar.open_time, Direction.LONG, "test", "feat-v1", "enter")]
        if stamp == self.exit_at and position.direction is Direction.LONG:
            return [Signal(bar.open_time, Direction.FLAT, "test", "feat-v1", "exit")]
        return []


COST = CostModel(
    cost_model_id="t", fee_rate=0.001, slippage_bps=10.0, min_notional=5.0,
    funding_included=False,
)
BARS = _bars([100.0, 105.0, 110.0, 115.0, 120.0], opens=[100.0, 110.0, 120.0, 130.0, 140.0])


def test_fill_price_is_next_open_with_slippage() -> None:
    sim = simulate(BARS, AtTimes(TS[1], TS[3]), {}, COST, initial_cash=1000.0)
    assert len(sim.trades) == 1
    trade = sim.trades[0]
    # TS[1] 出信号 → TS[2] 开盘 120 成交（+10bps）；TS[3] 出平仓信号 → TS[4] 开盘 140 成交（-10bps）
    assert trade.entry_price == pytest.approx(120.0 * 1.001)
    assert trade.exit_price == pytest.approx(140.0 * 0.999)
    assert trade.entry_time == TS[2] and trade.exit_time == TS[4]


def test_fees_are_charged_on_both_sides() -> None:
    sim = simulate(BARS, AtTimes(TS[1], TS[3]), {}, COST, initial_cash=1000.0)
    trade = sim.trades[0]
    expected = trade.entry_price * trade.qty * 0.001 + trade.exit_price * trade.qty * 0.001
    assert trade.fees == pytest.approx(expected)


def test_no_trades_keeps_equity_flat() -> None:
    bars = _bars([100.0] * 5)
    sim = simulate(bars, AtTimes(TS[0], TS[0]), {}, COST, initial_cash=1000.0)
    assert sim.trades == ()
    assert sim.equity.iloc[-1] == pytest.approx(1000.0)


def test_equity_length_matches_bars() -> None:
    sim = simulate(BARS, AtTimes(TS[1], TS[2]), {}, COST)
    assert len(sim.equity) == len(BARS)
    assert sim.equity.index.equals(pd.DatetimeIndex(BARS["open_time"]))


def test_min_notional_blocks_tiny_order() -> None:
    sim = simulate(_bars([1.0] * 5), AtTimes(TS[1], TS[3]), {}, COST, initial_cash=1.0)
    assert sim.trades == ()  # 1 USDT 连最小下单额 5 USDT 都不够


def test_position_is_closed_at_end_of_data() -> None:
    sim = simulate(BARS, AtTimes(TS[1], TS[99]), {}, COST, initial_cash=1000.0)
    assert len(sim.trades) == 1
    # 数据末尾强制平仓，按最后一根收盘价
    assert sim.trades[0].exit_price == pytest.approx(120.0 * 0.999)


def test_exposure_is_bounded_by_one() -> None:
    sim = simulate(BARS, AtTimes(TS[1], TS[3]), {}, COST, initial_cash=1000.0)
    assert sim.exposure.max() <= 1.0
    assert sim.exposure.min() >= 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_simulator.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'keel.research.simulator'`

- [ ] **Step 3: 实现**

`backend/keel/research/simulator.py`:

```python
"""逐 bar 执行器（engine=keel-bar，见计划 D2）。

三条不能改的规则：
1. **成交发生在下一根 bar 的开盘价**。用当前 bar 收盘价成交等于偷看未来——
   这是回测最常见也最难自查的偏差，所以它写在校验里而不是文档里。
2. 滑点方向永远不利于自己：买入加、卖出减。
3. 一切窗口构造都走 ``run_decide``，不在这里手写 ``iloc`` —— 契约的执行只有一个地方，
   执行器自己切一次窗口，早晚会和契约里的那次算出不同结果。

这里只做多头（V1–V2 现货，ADR-001）。做空要等永续，那是另一套保证金模型。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from keel.contracts import (
    CostModel,
    Direction,
    InsufficientHistory,
    PositionView,
    run_decide,
)

BPS = 1e-4


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    entry_time: pd.Timestamp | None
    exit_time: pd.Timestamp | None
    direction: str
    qty: float
    entry_price: float
    exit_price: float | None
    pnl: float
    fees: float


@dataclass(frozen=True, slots=True)
class Simulation:
    trades: tuple[Trade, ...]
    equity: pd.Series
    exposure: pd.Series


def _fill(price: float, side: str, slippage_bps: float) -> float:
    slip = price * slippage_bps * BPS
    return price + slip if side == "buy" else price - slip


def simulate(
    bars: pd.DataFrame,
    strategy,
    params: dict,
    cost: CostModel,
    *,
    initial_cash: float = 10_000.0,
    state: dict | None = None,
    feature_version: str = "feat-v1",
) -> Simulation:
    if bars.empty:
        empty = pd.Series(dtype="float64")
        return Simulation((), empty, empty)

    frame = bars.reset_index(drop=True).sort_values("open_time").reset_index(drop=True)
    symbol = str(frame["symbol"].iloc[0])
    state = state if state is not None else {}
    cash = float(initial_cash)
    qty = 0.0
    entry_price: float | None = None
    entry_time: pd.Timestamp | None = None
    entry_index = 0
    entry_fees = 0.0
    trades: list[Trade] = []
    # 下一根开盘要执行的动作队列。用队列而不是单个槽位：
    # 同一根 bar 出「平旧仓 + 开新仓」是常见的，一个槽位会静默丢掉第二条。
    pending: list[Direction] = []
    equity: list[float] = []
    exposure: list[float] = []

    def close_position(price: float, when: pd.Timestamp) -> None:
        nonlocal cash, qty, entry_price, entry_time, entry_fees
        if qty == 0.0 or entry_price is None:
            return
        fill = _fill(price, "sell", float(cost.slippage_bps))
        fee = fill * qty * float(cost.fee_rate)
        cash += fill * qty - fee
        fees = entry_fees + fee
        trades.append(
            Trade(
                symbol,
                entry_time,
                when,
                "LONG",
                qty,
                entry_price,
                fill,
                (fill - entry_price) * qty - fees,
                fees,
            )
        )
        qty, entry_price, entry_time, entry_fees = 0.0, None, None, 0.0

    for i in range(len(frame)):
        row = frame.iloc[i]
        ts = pd.Timestamp(row["open_time"])

        while pending:
            direction = pending.pop(0)
            if direction is Direction.LONG and qty == 0.0:
                fill = _fill(float(row["open"]), "buy", float(cost.slippage_bps))
                # 分母含手续费：按 cash/fill 算会多买一手，cash 变负、敞口算出来 > 1
                size = cash / (fill * (1.0 + float(cost.fee_rate)))
                if size > 0 and fill * size >= float(cost.min_notional):
                    fee = fill * size * float(cost.fee_rate)
                    qty, entry_price, entry_time, entry_fees = size, fill, ts, fee
                    entry_index = i
                    cash -= fill * size + fee
            elif direction is Direction.FLAT and qty > 0.0:
                close_position(float(row["open"]), ts)

        mark = float(row["close"])
        equity.append(cash + qty * mark)
        # 现货不能加杠杆，敞口上限就是 1；clamp 只为吸收浮点误差
        exposure.append(min(1.0, (qty * mark) / (cash + qty * mark)) if qty else 0.0)

        position = PositionView(
            instrument_id=symbol,
            direction=Direction.LONG if qty else Direction.FLAT,
            qty=qty,
            entry_price=entry_price,
            unrealized_pnl=(mark - entry_price) * qty if entry_price else 0.0,
            holding_bars=(i - entry_index) if qty else 0,
        )
        # 索引必须是时间：make_bar_view 会拒绝无时区的索引。
        stamped = frame.iloc[: i + 1].set_index("open_time")
        try:
            signals = run_decide(
                strategy, stamped, params, position, state, feature_version=feature_version
            )
        except InsufficientHistory:
            # 历史不足不是错误，跳过这根 bar（契约文档明说了这一点）。
            continue
        for signal in signals:
            pending.append(signal.direction)

    if qty and len(frame):
        close_position(float(frame["close"].iloc[-1]), pd.Timestamp(frame["open_time"].iloc[-1]))

    index = pd.DatetimeIndex(frame["open_time"])
    return Simulation(
        tuple(trades),
        pd.Series(equity, index=index),
        pd.Series(exposure, index=index),
    )
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_simulator.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/research/simulator.py backend/tests/test_simulator.py
git commit -m "feat(research): 逐 bar 执行器（下一根开盘成交）"
```

---

## Task 12: 指标与引擎入口

**Files:**
- Modify: `backend/keel/research/engine.py`（加 `KEEL_BAR`、`run`）
- Create: `backend/keel/research/metrics.py`
- Test: `backend/tests/test_metrics.py`

**Interfaces:**
- Produces:
  - `keel.research.metrics.compute_metrics(equity: pd.Series, trades, interval: str) -> dict`（键：`total_return`/`cagr`/`sharpe`/`max_drawdown`/`win_rate`/`trade_count`/`turnover`）
  - `keel.research.engine.Engine.KEEL_BAR = "keel-bar"`
  - `keel.research.engine.run(engine: Engine, request: BacktestRequest) -> dict`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_metrics.py`:

```python
"""指标：年化因子显式、最大回撤符号正确。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from keel.research.metrics import compute_metrics


def _equity(values: list[float]) -> pd.Series:
    idx = pd.date_range("2023-01-01", periods=len(values), freq="1D", tz="UTC")
    return pd.Series(values, index=idx)


def test_total_return() -> None:
    m = compute_metrics(_equity([100.0, 110.0]), (), "1d")
    assert m["total_return"] == pytest.approx(0.10)


def test_max_drawdown_is_positive_magnitude() -> None:
    m = compute_metrics(_equity([100.0, 120.0, 60.0, 90.0]), (), "1d")
    assert m["max_drawdown"] == pytest.approx(0.5)


def test_cagr_uses_explicit_periods() -> None:
    # 365 天翻倍 → 年化 100%；用 252 会算成 ~1.9
    values = list(np.linspace(100.0, 200.0, 366))
    m = compute_metrics(_equity(values), (), "1d")
    assert m["cagr"] == pytest.approx(1.0, rel=0.02)


def test_sharpe_is_zero_for_flat_equity() -> None:
    m = compute_metrics(_equity([100.0] * 10), (), "1d")
    assert m["sharpe"] == 0.0


def test_trade_count_and_win_rate() -> None:
    class T:
        def __init__(self, pnl: float) -> None:
            self.pnl = pnl

    m = compute_metrics(_equity([100.0, 101.0]), (T(5.0), T(-2.0), T(1.0)), "1d")
    assert m["trade_count"] == 3
    assert m["win_rate"] == pytest.approx(2 / 3)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_metrics.py -v`
Expected: FAIL —— `ModuleNotFoundError`

- [ ] **Step 3: 实现**

`backend/keel/research/metrics.py`:

```python
"""回测指标。自己算，不调 QuantStats——指标是验收标准（A3/A4），
必须能逐行解释；QuantStats 只用于出报告。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from keel.reports.quantstats import periods_per_year


def _returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def compute_metrics(equity: pd.Series, trades, interval: str) -> dict:
    if len(equity) < 2:
        return {
            "total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
            "win_rate": 0.0, "trade_count": len(trades), "turnover": 0.0,
        }
    rets = _returns(equity)
    periods = periods_per_year(interval)  # type: ignore[arg-type]
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    years = len(equity) / periods
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0
    std = float(rets.std(ddof=0))
    sharpe = float(rets.mean() / std * math.sqrt(periods)) if std > 0 else 0.0
    running_max = equity.cummax()
    drawdown = float((1 - equity / running_max).max())
    pnls = [float(t.pnl) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": drawdown,
        "win_rate": (wins / len(pnls)) if pnls else 0.0,
        "trade_count": len(pnls),
        "turnover": float(np.sum([abs(p) for p in pnls])) / float(equity.iloc[0]),
    }
```

`backend/keel/research/engine.py` 的 `Engine` 加 `KEEL_BAR = "keel-bar"`，`run` 替换为：

```python
def run(engine: Engine, request) -> dict:
    """按引擎跑一次回测。V1 只有 ``keel-bar``（计划 D2）。"""
    if engine is not Engine.KEEL_BAR:
        raise NotImplementedError(f"engine {engine} 不在 V1 范围（计划 D2）")
    from keel.research.metrics import compute_metrics
    from keel.research.simulator import simulate

    sim = simulate(
        request.bars, request.strategy, request.params, request.cost,
        initial_cash=request.initial_cash,
    )
    metrics = compute_metrics(sim.equity, sim.trades, request.interval)
    return {
        "metrics": metrics,
        "equity": {str(k): float(v) for k, v in sim.equity.items()},
        "trades": [asdict(t) for t in sim.trades],
    }
```

顶部补 `from dataclasses import asdict`。

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_metrics.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/research/metrics.py backend/keel/research/engine.py backend/tests/test_metrics.py
git commit -m "feat(research): 回测指标与引擎入口"
```

---

## Task 13: 回测作业队列

**Files:**
- Modify: `backend/keel/research/backtest.py:38-40`
- Test: `backend/tests/test_queue.py`

**Interfaces:**
- Consumes: `keel.db.pool.transaction`
- Produces:
  - `enqueue(*, universe: list[str], interval: str, start: datetime, end: datetime, strategy_key: str, strategy_version: int, params: dict, cost_model_id: str, engine: str = "keel-bar", idempotency_key: str | None = None, data_snapshot_id: str | None = None) -> str`（返回 `run_id`）
  - `claim_next(*, timeout_seconds: int = 1800) -> BacktestRun | None`
  - `finish(run_id: str, *, metrics: dict | None = None, error: str | None = None) -> None`
  - `sweep_orphans(*, timeout_seconds: int = 1800) -> int`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_queue.py`:

```python
"""回测作业队列：入队幂等、认领排他、孤儿回收。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from keel.research import backtest


@pytest.fixture(autouse=True)
def _clean(pg_dsn: str) -> None:
    import psycopg

    from keel.db import migrations

    migrations.migrate(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("delete from backtest_runs")
        conn.commit()
    import os

    os.environ["KEEL_PG_DSN"] = pg_dsn
    from keel.config import get_settings
    from keel.db import pool

    get_settings.cache_clear()
    pool.reset_pool()


def _enqueue(**kw):
    base = {
        "universe": ["BTC/USDT"],
        "interval": "1h",
        "start": datetime(2023, 1, 1, tzinfo=UTC),
        "end": datetime(2023, 6, 1, tzinfo=UTC),
        "strategy_key": "sma_cross",
        "strategy_version": 1,
        "params": {"fast": 20, "slow": 60},
        "cost_model_id": "default",
    }
    base.update(kw)
    return backtest.enqueue(**base)


def test_enqueue_returns_queued_run(pg_dsn: str) -> None:
    run_id = _enqueue()
    run = backtest.get(run_id)
    assert run.status is backtest.RunStatus.QUEUED
    assert run.strategy_key == "sma_cross"


def test_idempotency_key_returns_same_run(pg_dsn: str) -> None:
    first = _enqueue(idempotency_key="abc")
    second = _enqueue(idempotency_key="abc")
    assert first == second


def test_claim_is_exclusive(pg_dsn: str) -> None:
    run_id = _enqueue()
    claimed = backtest.claim_next()
    assert claimed is not None and claimed.run_id == run_id
    assert claimed.status is backtest.RunStatus.RUNNING
    assert backtest.claim_next() is None


def test_finish_marks_succeeded(pg_dsn: str) -> None:
    run_id = _enqueue()
    backtest.claim_next()
    backtest.finish(run_id, metrics={"sharpe": 1.2})
    run = backtest.get(run_id)
    assert run.status is backtest.RunStatus.SUCCEEDED
    assert run.metrics == {"sharpe": 1.2}


def test_sweep_orphans_reclaims_stale_running(pg_dsn: str) -> None:
    import psycopg

    run_id = _enqueue()
    backtest.claim_next()
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "update backtest_runs set started_at = now() - interval '10 hours' where run_id = %s",
            (run_id,),
        )
        conn.commit()
    assert backtest.sweep_orphans(timeout_seconds=60) == 1
    assert backtest.get(run_id).status is backtest.RunStatus.FAILED
    assert backtest.get(run_id).fail_reason is backtest.FailReason.ORPHANED
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && KEEL_TEST_PG_DSN="postgresql://keel:keel@127.0.0.1:5432/keel" UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_queue.py -v`
Expected: FAIL —— `AttributeError: module 'keel.research.backtest' has no attribute 'enqueue'`

- [ ] **Step 3: 实现**

`backend/keel/research/backtest.py` 的 dataclass 换成（字段与表对齐）：

```python
@dataclass(frozen=True, slots=True)
class BacktestRun:
    run_id: str
    status: RunStatus
    universe: list[str]
    interval: str
    start_time: datetime
    end_time: datetime
    strategy_key: str
    strategy_version: int
    params: dict
    cost_model_id: str
    engine: str
    fail_reason: FailReason | None = None
    metrics: dict | None = None
```

函数实现：

```python
def enqueue(**kw) -> str:
    """写一条 QUEUED 并返回 run_id。同一 Idempotency-Key 复用已有 run。"""
    import json
    import uuid

    from keel.db.pool import transaction

    key = kw.pop("idempotency_key", None)
    run_id = uuid.uuid4().hex[:16]
    with transaction() as conn:
        if key:
            existing = conn.execute(
                "select run_id from backtest_runs where idempotency_key = %s", (key,)
            ).fetchone()
            if existing:
                return existing["run_id"]
        conn.execute(
            """
            insert into backtest_runs
              (run_id, strategy_config_id, symbol, interval, start_time, end_time,
               universe, strategy_key, strategy_version, params, cost_model_id,
               engine, data_snapshot_id, idempotency_key)
            values (%s, null, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s, %s)
            """,
            (
                run_id, kw["universe"][0], kw["interval"], kw["start"], kw["end"],
                json.dumps(kw["universe"]), kw["strategy_key"], kw["strategy_version"],
                json.dumps(kw["params"]), kw["cost_model_id"], kw["engine"],
                kw.get("data_snapshot_id"), key,
            ),
        )
    return run_id


def get(run_id: str) -> BacktestRun:
    from keel.db.pool import connection

    with connection() as conn:
        row = conn.execute("select * from backtest_runs where run_id = %s", (run_id,)).fetchone()
    if row is None:
        from keel.errors import NotFound

        raise NotFound(f"backtest run not found: {run_id}")
    return _to_run(row)


def claim_next(*, timeout_seconds: int = 1800) -> BacktestRun | None:
    from keel.db.pool import transaction

    with transaction() as conn:
        row = conn.execute(
            """
            select * from backtest_runs
             where status = 'QUEUED'
             order by created_at
             for update skip locked
             limit 1
            """
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "update backtest_runs set status = 'RUNNING', started_at = now() where run_id = %s",
            (row["run_id"],),
        )
    row["status"] = "RUNNING"
    return _to_run(row)


def finish(run_id: str, *, metrics: dict | None = None, error: str | None = None) -> None:
    import json

    from keel.db.pool import transaction

    status = "FAILED" if error else "SUCCEEDED"
    with transaction() as conn:
        conn.execute(
            """update backtest_runs
                  set status = %s, finished_at = now(), metrics = %s::jsonb,
                      error = %s, fail_reason = case when %s is null then null else 'ERROR' end
                where run_id = %s""",
            (status, json.dumps(metrics or {}), error, error, run_id),
        )


def sweep_orphans(*, timeout_seconds: int = 1800) -> int:
    from keel.db.pool import transaction

    with transaction() as conn:
        rows = conn.execute(
            """update backtest_runs
                  set status = 'FAILED', fail_reason = 'ORPHANED', finished_at = now()
                where status = 'RUNNING'
                  and started_at < now() - make_interval(secs => %s)
            returning run_id""",
            (timeout_seconds * 2,),
        ).fetchall()
    return len(rows)
```

`_to_run(row: dict) -> BacktestRun` 负责把 `universe`/`params`/`metrics` 从 jsonb 反序列化（psycopg 已经给成 dict/list，直接透传即可）并 `RunStatus(row["status"])`。

- [ ] **Step 4: 跑测试**

Run: `cd backend && KEEL_TEST_PG_DSN="postgresql://keel:keel@127.0.0.1:5432/keel" UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_queue.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/research/backtest.py backend/tests/test_queue.py
git commit -m "feat(research): 回测作业队列与孤儿回收"
```

---

## Task 14: 回测 worker 装配

**Files:**
- Modify: `backend/keel/workers/backtest_worker.py`
- Create: `backend/keel/research/runner.py`
- Test: `backend/tests/test_runner.py`

**Interfaces:**
- Consumes: `keel.research.backtest.{claim_next, finish, sweep_orphans}`、`keel.research.registry.get`、`keel.data.lake.read_bars`、`keel.db.mart.connect`、`keel.research.simulator.simulate`
- Produces:
  - `keel.research.runner.run_once(*, raw_root: Path | None = None, mart: Path | None = None) -> bool`（认领并跑完一条；无任务返回 `False`）
  - `keel.research.runner.execute_run(run) -> dict`（不碰数据库，纯计算，便于测试）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_runner.py`:

```python
"""执行一条回测：读原始湖 → 逐 bar 模拟 → 返回 metrics。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from keel.data.datasets import ODS_BARS
from keel.data.lake import partition_dir, write_partition
from keel.market.normalize import normalize_ohlcv
from keel.research.backtest import BacktestRun, RunStatus
from keel.research.runner import execute_run

HOUR = 3_600_000


def _seed(root: Path, n: int = 200) -> None:
    import math

    rows = []
    for i in range(n):
        px = 100 + 10 * math.sin(i / 12)
        rows.append([1_699_200_000_000 + i * HOUR, px, px * 1.01, px * 0.99, px, 5.0])
    frame = normalize_ohlcv(rows, "BTC/USDT", "1h")
    write_partition(partition_dir(root, ODS_BARS, "BTC/USDT", "1h",
                                  datetime(2023, 11, 1, tzinfo=UTC)), frame)


def test_execute_run_produces_metrics(tmp_path: Path) -> None:
    _seed(tmp_path)
    run = BacktestRun(
        run_id="r1",
        status=RunStatus.RUNNING,
        universe=["BTC/USDT"],
        interval="1h",
        start_time=datetime(2023, 11, 1, tzinfo=UTC),
        end_time=datetime(2023, 11, 20, tzinfo=UTC),
        strategy_key="sma_cross",
        strategy_version=1,
        params={"fast": 5, "slow": 20},
        cost_model_id="default",
        engine="keel-bar",
    )
    out = execute_run(run, raw_root=tmp_path)
    assert set(out["metrics"]) >= {"sharpe", "max_drawdown", "trade_count"}
    assert out["bars"] > 100


def test_execute_run_rejects_unknown_strategy(tmp_path: Path) -> None:
    _seed(tmp_path, 10)
    run = BacktestRun(
        run_id="r2", status=RunStatus.RUNNING, universe=["BTC/USDT"], interval="1h",
        start_time=datetime(2023, 11, 1, tzinfo=UTC),
        end_time=datetime(2023, 11, 2, tzinfo=UTC),
        strategy_key="nope", strategy_version=1, params={}, cost_model_id="default",
        engine="keel-bar",
    )
    with pytest.raises(KeyError):
        execute_run(run, raw_root=tmp_path)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_runner.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'keel.research.runner'`

- [ ] **Step 3: 实现**

`backend/keel/research/runner.py`:

```python
"""把一条回测作业跑完。

职责边界：这里**不写数据库**。``execute_run`` 是纯计算，``run_once`` 负责
认领/落账。拆开的原因很简单：失败要能被测试，而测试不该连着一个真库。
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from keel.contracts import CostModel
from keel.data.lake import read_bars
from keel.research.metrics import compute_metrics
from keel.research.registry import get as get_strategy
from keel.research.simulator import simulate

DEFAULT_COST = CostModel(
    cost_model_id="default",
    fee_rate=0.001,
    slippage_bps=5.0,
    min_notional=5.0,
    funding_included=False,
)


def _cost_model(cost_model_id: str) -> CostModel:
    """V1 只有内置默认模型；从 PG 读表是 V1.5 的事（cost_models 已建好表）。"""
    return DEFAULT_COST


def execute_run(run, *, raw_root: Path | None = None) -> dict:
    from keel.config import get_settings

    root = Path(raw_root or get_settings().raw_root)
    strategy = get_strategy(run.strategy_key, run.strategy_version)
    cost = _cost_model(run.cost_model_id)
    per_symbol = []
    for symbol in run.universe:
        bars = read_bars(root, symbol=symbol, interval=run.interval)
        bars = bars[(bars["open_time"] >= run.start_time) & (bars["open_time"] <= run.end_time)]
        if bars.empty:
            continue
        sim = simulate(bars, strategy, run.params, cost)
        per_symbol.append((sim, compute_metrics(sim.equity, sim.trades, run.interval)))
    if not per_symbol:
        raise ValueError(f"no bars for universe {run.universe}")
    # 等权组合：各标的收益率取平均
    equity = sum(s.equity / s.equity.iloc[0] for s, _ in per_symbol) / len(per_symbol)
    equity = equity * per_symbol[0][0].equity.iloc[0]
    trades = tuple(t for s, _ in per_symbol for t in s.trades)
    return {
        "metrics": compute_metrics(equity, trades, run.interval),
        "per_symbol": {s.equity.name or str(i): m for i, (s, m) in enumerate(per_symbol)},
        "bars": sum(len(s.equity) for s, _ in per_symbol),
        "equity": {str(k): float(v) for k, v in equity.items()},
        # Trade 是 frozen slots dataclass，没有 __dict__；用 asdict。
        "trades": [asdict(t) for t in trades],
    }


def run_once(*, raw_root: Path | None = None) -> bool:
    """认领一条并跑完。没有任务返回 False。"""
    from keel.research import backtest

    backtest.sweep_orphans()
    run = backtest.claim_next()
    if run is None:
        return False
    try:
        result = execute_run(run, raw_root=raw_root)
        backtest.finish(run.run_id, metrics=result["metrics"])
    except Exception as exc:
        backtest.finish(run.run_id, error=f"{type(exc).__name__}: {exc}")
        raise
    return True
```

`backend/keel/workers/backtest_worker.py` 的循环体换成：

```python
        try:
            heartbeat.beat("backtest-worker")
            if runner.run_once():
                continue
            time.sleep(2)
        except Exception:
            log.exception("backtest round failed")
            time.sleep(5)
```

并补 import `from keel.research import runner`。

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_runner.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/research/runner.py backend/keel/workers/backtest_worker.py backend/tests/test_runner.py
git commit -m "feat(research): 回测 worker 装配"
```

---

## Task 15: 报告与制品

**Files:**
- Modify: `backend/keel/reports/quantstats.py:32-34`
- Create: `backend/keel/reports/artifacts.py`
- Test: `backend/tests/test_reports.py`

**Interfaces:**
- Produces:
  - `keel.reports.quantstats.build_report(returns: pd.Series, interval: str) -> dict`（键：`cagr`/`sharpe`/`sortino`/`max_drawdown`/`calmar`/`volatility`/`curve`）
  - `keel.reports.artifacts.write_artifact(kind: str, payload: dict, *, run_id: str | None = None, root: Path | None = None) -> str`（返回 `artifact_id`）
  - `keel.reports.artifacts.resolve(artifact_id: str) -> Path`
  - `keel.reports.artifacts.content_url(artifact_id: str) -> str`（`/api/v1/artifacts/{id}`）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_reports.py`:

```python
"""报告指标与制品落地。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from keel.reports import artifacts, quantstats


def _returns(n: int = 400, mu: float = 0.0004) -> pd.Series:
    idx = pd.date_range("2023-01-01", periods=n, freq="1D", tz="UTC")
    rng = np.random.default_rng(7)
    return pd.Series(rng.normal(mu, 0.01, n), index=idx)


def test_report_has_required_keys() -> None:
    rep = quantstats.build_report(_returns(), "1d")
    assert {"cagr", "sharpe", "sortino", "max_drawdown", "calmar", "volatility", "curve"} <= set(rep)
    assert len(rep["curve"]) == 400


def test_report_marks_simulated() -> None:
    rep = quantstats.build_report(_returns(), "1d")
    # A8：报告必须自带"模拟回测"标记，不能靠前端补
    assert rep["disclaimer"] == "模拟回测，非实盘业绩"


def test_build_report_rejects_unsupported_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        quantstats.build_report(_returns(), "7m")


def test_artifact_roundtrip(tmp_path: Path) -> None:
    artifact_id = artifacts.write_artifact("report_json", {"a": 1}, root=tmp_path)
    path = artifacts.resolve(artifact_id, root=tmp_path)
    assert json.loads(path.read_text())["a"] == 1
    assert artifacts.content_url(artifact_id) == f"/api/v1/artifacts/{artifact_id}"


def test_artifact_id_is_not_a_path(tmp_path: Path) -> None:
    artifact_id = artifacts.write_artifact("report_json", {"a": 1}, root=tmp_path)
    assert "/" not in artifact_id and ".." not in artifact_id
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_reports.py -v`
Expected: FAIL —— `NotImplementedError: 报告生成尚未实现`

- [ ] **Step 3: 实现**

`backend/keel/reports/quantstats.py` 的 `build_report` 替换为：

```python
def build_report(returns, interval: str) -> dict:
    """把日/小时收益序列变成报告字典。返回数据，不返回 HTML。"""
    import numpy as np
    import pandas as pd

    try:
        periods = PERIODS_PER_YEAR[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported interval: {interval}") from exc
    series = pd.Series(returns).dropna()
    if series.empty:
        return {
            "cagr": 0.0, "sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0,
            "calmar": 0.0, "volatility": 0.0, "curve": [], "periods_per_year": periods,
            "disclaimer": "模拟回测，非实盘业绩",
        }
    equity = (1.0 + series).cumprod()
    years = len(series) / periods
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0
    vol = float(series.std(ddof=0) * np.sqrt(periods))
    sharpe = float(series.mean() / series.std(ddof=0) * np.sqrt(periods)) if series.std(ddof=0) else 0.0
    downside = series[series < 0].std(ddof=0)
    sortino = float(series.mean() / downside * np.sqrt(periods)) if downside else 0.0
    drawdown = float((1 - equity / equity.cummax()).max())
    calmar = float(cagr / drawdown) if drawdown > 0 else 0.0
    return {
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": drawdown,
        "calmar": calmar,
        "volatility": vol,
        "periods_per_year": periods,
        "curve": [float(v) for v in equity],
        "disclaimer": "模拟回测，非实盘业绩",
    }
```

`backend/keel/reports/artifacts.py`:

```python
"""制品库。文件放磁盘，元数据放 PG；接口只暴露 ``content_url``。

绝不返回绝对路径：那等于把部署细节写进协议，换存储就要破接口（§3.11）。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path


def _root(root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    from keel.config import get_settings

    return Path(get_settings().artifact_root)


def write_artifact(
    kind: str, payload: dict, *, run_id: str | None = None, root: Path | None = None
) -> str:
    artifact_id = uuid.uuid4().hex
    directory = _root(root) / artifact_id[:2]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{artifact_id}.{kind}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return artifact_id


def resolve(artifact_id: str, *, root: Path | None = None) -> Path:
    if "/" in artifact_id or ".." in artifact_id:
        from keel.errors import NotFound

        raise NotFound(f"bad artifact id: {artifact_id}")
    directory = _root(root) / artifact_id[:2]
    matches = sorted(directory.glob(f"{artifact_id}.*.json"))
    if not matches:
        from keel.errors import NotFound

        raise NotFound(f"artifact not found: {artifact_id}")
    return matches[0]


def content_url(artifact_id: str) -> str:
    return f"/api/v1/artifacts/{artifact_id}"
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_reports.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/reports backend/tests/test_reports.py
git commit -m "feat(reports): 报告指标与制品库"
```

---

## Task 16: API-01/02 行情与覆盖

**Files:**
- Create: `backend/keel/api/downsample.py`
- Modify: `backend/keel/api/routers/market.py`
- Modify: `backend/tests/test_api.py`（参数名改 `from`/`to`）
- Test: `backend/tests/test_api_market.py`

**Interfaces:**
- Consumes: `keel.db.mart.connect`、DuckDB 表 `dwd_bars`/`ops_coverage`
- Produces:
  - `keel.api.downsample.downsample(frame: pd.DataFrame, bucket_ms: int) -> pd.DataFrame`（OHLCV 语义固定）
  - `GET /api/v1/bars?symbol&interval&from&to&limit&max_points` → `Envelope[list[Bar]]`
  - `GET /api/v1/coverage?symbol&interval` → `Envelope[dict]`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_api_market.py`:

```python
"""API-01/02：形状、降采样语义、stale 标记。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from keel.api.app import create_app
from keel.db import mart

HOUR = 3_600_000


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    target = tmp_path / "keel.duckdb"

    def build(path: Path) -> None:
        con = duckdb.connect(str(path))
        con.execute(
            """
            create table dwd_bars as
            select * from (values
              (timestamptz '2023-11-01 00:00:00+00', 'BTC/USDT', '1h', 1.0, 2.0, 0.5, 1.5, 10.0, 1, false),
              (timestamptz '2023-11-01 01:00:00+00', 'BTC/USDT', '1h', 1.5, 3.0, 1.4, 2.5, 20.0, 1, false),
              (timestamptz '2023-11-01 03:00:00+00', 'BTC/USDT', '1h', 2.5, 4.0, 2.0, 3.5, 30.0, 1, false)
            ) as t(open_time, symbol, interval, open, high, low, close, volume, revision, is_outlier)
            """
        )
        con.execute(
            """
            create table ops_coverage as
            select 'BTC/USDT' as symbol, '1h' as interval,
                   timestamptz '2023-11-01 00:00:00+00' as start,
                   timestamptz '2023-11-01 03:00:00+00' as "end",
                   4 as expected, 3 as present, 0 as blocking_gaps,
                   '[{"missing": 1, "severity": "tolerated"}]' as gaps
            """
        )
        con.close()

    mart.publish(build, target)
    monkeypatch.setenv("KEEL_DUCKDB_PATH", str(target))
    from keel.config import get_settings

    get_settings.cache_clear()
    return TestClient(create_app())


def test_bars_returns_envelope(client: TestClient) -> None:
    r = client.get(
        "/api/v1/bars",
        params={"symbol": "BTC/USDT", "interval": "1h",
                "from": "2023-11-01T00:00:00Z", "to": "2023-11-02T00:00:00Z"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 3
    assert body["data"][0]["close"] == 1.5
    assert body["meta"]["as_of"].startswith("2023-11-01T03:00:00")


def test_bars_max_points_downsamples(client: TestClient) -> None:
    r = client.get(
        "/api/v1/bars",
        params={"symbol": "BTC/USDT", "interval": "1h",
                "from": "2023-11-01T00:00:00Z", "to": "2023-11-02T00:00:00Z",
                "max_points": 2},
    )
    data = r.json()["data"]
    # 3 根凑 2 个点 → 桶宽 = ceil(3/2) × 1h = 2h，得到 00:00 与 02:00 两个桶
    assert len(data) == 2
    # open=桶内首值 high=max low=min close=末值 volume=sum
    assert data[0]["open"] == 1.0
    assert data[0]["high"] == 3.0
    assert data[0]["low"] == 0.5
    assert data[0]["close"] == 2.5
    assert data[0]["volume"] == 30.0


def test_bars_unknown_symbol_is_empty_not_error(client: TestClient) -> None:
    r = client.get(
        "/api/v1/bars",
        params={"symbol": "NOPE/USDT", "interval": "1h",
                "from": "2023-11-01T00:00:00Z", "to": "2023-11-02T00:00:00Z"},
    )
    assert r.status_code == 200 and r.json()["data"] == []


def test_coverage_returns_gaps(client: TestClient) -> None:
    r = client.get("/api/v1/coverage", params={"symbol": "BTC/USDT", "interval": "1h"})
    body = r.json()
    assert body["data"]["present"] == 3
    assert body["data"]["gaps"][0]["severity"] == "tolerated"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_api_market.py -v`
Expected: FAIL —— 501 NOT_IMPLEMENTED

- [ ] **Step 3: 实现降采样**

`backend/keel/api/downsample.py`:

```python
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
```

- [ ] **Step 4: 实现两个端点**

`backend/keel/api/routers/market.py` 整体替换为：

```python
"""行情查询接口（API-01 K 线、API-02 覆盖范围）。"""

from __future__ import annotations

from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, Query

from keel.api.deps import API_PREFIX, require_token
from keel.api.downsample import downsample
from keel.api.schemas import Bar, Envelope, Meta
from keel.db.mart import connect
from keel.market.normalize import interval_to_ms

router = APIRouter(prefix=API_PREFIX, tags=["market"], dependencies=[Depends(require_token)])

MAX_POINTS_CAP = 10_000


@router.get("/bars", response_model=Envelope[list[Bar]])
def get_bars(
    symbol: Annotated[str, Query(description="如 BTC/USDT")],
    interval: Annotated[str, Query(description="1m/5m/15m/1h/4h/1d")],
    start: Annotated[str, Query(alias="from", description="ISO8601，含")],
    end: Annotated[str, Query(alias="to", description="ISO8601，含")],
    limit: Annotated[int, Query(ge=1, le=50_000)] = 5000,
    max_points: Annotated[int, Query(ge=2, le=MAX_POINTS_CAP)] = 2000,
) -> Envelope[list[Bar]]:
    with connect() as con:
        frame = con.execute(
            """select open_time, open, high, low, close, volume, revision
                 from dwd_bars
                where symbol = ? and interval = ?
                  and open_time >= ? and open_time <= ?
                order by open_time
                limit ?""",
            [symbol, interval, pd.Timestamp(start), pd.Timestamp(end), limit],
        ).df()
    # DuckDB 的 timestamptz 按会话时区返回（本机是 +08:00）。接口约定是 UTC，
    # 这里统一转一次——否则同一份数据在 UTC 机器与东八区机器上返回不同字符串。
    if len(frame):
        frame["open_time"] = frame["open_time"].dt.tz_convert("UTC")
    step = interval_to_ms(interval)
    if len(frame) > max_points:
        bucket = step * -(-len(frame) // max_points)
        frame = downsample(frame, bucket)
    data = [
        Bar(
            open_time=row.open_time.to_pydatetime(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in frame.itertuples()
    ]
    as_of = frame["open_time"].max().to_pydatetime() if len(frame) else None
    return Envelope(data=data, meta=Meta(as_of=as_of, stale=False))


@router.get("/coverage")
def get_coverage(
    symbol: Annotated[str, Query()],
    interval: Annotated[str, Query()],
) -> Envelope[dict]:
    with connect() as con:
        rows = con.execute(
            'select symbol, interval, start, "end", expected, present, blocking_gaps, gaps '
            "from ops_coverage where symbol = ? and interval = ?",
            [symbol, interval],
        ).df()
    if rows.empty:
        return Envelope(data={"symbol": symbol, "interval": interval, "gaps": []}, meta=Meta())
    row = rows.iloc[0]
    import json

    return Envelope(
        data={
            "symbol": row["symbol"],
            "interval": row["interval"],
            "start": row["start"],
            "end": row["end"],
            "expected": int(row["expected"]),
            "present": int(row["present"]),
            "blocking_gaps": int(row["blocking_gaps"]),
            "gaps": json.loads(row["gaps"]),
        },
        meta=Meta(as_of=None),
    )
```

`backend/tests/test_api.py` 里 `test_unimplemented_endpoints_use_unified_error_shape` 改为请求 `/api/v1/reports/xyz`（`/bars` 已实现，501 断言要换个还没实现的端点），参数名同步改成 `from`/`to`。

- [ ] **Step 5: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_api.py tests/test_api_market.py -v`
Expected: 全部 passed

- [ ] **Step 6: Commit**

```bash
git add backend/keel/api backend/tests/test_api.py backend/tests/test_api_market.py
git commit -m "feat(api): 行情与覆盖接口 API-01/02"
```

---

## Task 17: API-03/04/05/10/12 回测与报告

**Files:**
- Modify: `backend/keel/api/routers/backtests.py`
- Modify: `backend/keel/api/routers/reports.py`
- Test: `backend/tests/test_api_backtests.py`

**Interfaces:**
- Consumes: `keel.research.backtest.{enqueue, get, list_runs}`、`keel.reports.artifacts`
- Produces:
  - `POST /api/v1/backtests` → 201 `{data:{run_id, status:"QUEUED"}}`
  - `GET /api/v1/backtests/{run_id}`、`GET /api/v1/backtests?limit&cursor`
  - `GET /api/v1/reports/{run_id}`、`GET /api/v1/artifacts/{artifact_id}`
  - `keel.research.backtest.list_runs(limit: int = 50, cursor: str | None = None) -> list[BacktestRun]`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_api_backtests.py`:

```python
"""API-03/04/05：入队、幂等、契约字段。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from keel.api.app import create_app
from keel.db import migrations

BODY = {
    "strategy_key": "sma_cross",
    "strategy_version": 1,
    "universe": ["BTC/USDT"],
    "interval": "1h",
    "from": "2023-01-01T00:00:00Z",
    "to": "2023-06-01T00:00:00Z",
    "params": {"fast": 20, "slow": 60},
    "cost_model_id": "default",
    "engine": "keel-bar",
}


@pytest.fixture()
def client(pg_dsn: str, monkeypatch) -> TestClient:
    import psycopg

    migrations.migrate(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("delete from backtest_runs")
        conn.commit()
    monkeypatch.setenv("KEEL_PG_DSN", pg_dsn)
    from keel.config import get_settings
    from keel.db import pool

    get_settings.cache_clear()
    pool.reset_pool()
    return TestClient(create_app())


def test_submit_returns_queued(client: TestClient) -> None:
    r = client.post("/api/v1/backtests", json=BODY)
    assert r.status_code == 201
    assert r.json()["data"]["status"] == "QUEUED"
    assert r.json()["data"]["run_id"]


def test_submit_requires_cost_model(client: TestClient) -> None:
    body = dict(BODY)
    body.pop("cost_model_id")
    assert client.post("/api/v1/backtests", json=body).status_code == 422


def test_submit_requires_universe(client: TestClient) -> None:
    body = dict(BODY, universe=[])
    assert client.post("/api/v1/backtests", json=body).status_code == 422


def test_idempotency_key_reuses_run(client: TestClient) -> None:
    first = client.post("/api/v1/backtests", json=BODY, headers={"Idempotency-Key": "k1"})
    second = client.post("/api/v1/backtests", json=BODY, headers={"Idempotency-Key": "k1"})
    assert first.json()["data"]["run_id"] == second.json()["data"]["run_id"]


def test_get_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/backtests/nope").status_code == 404


def test_list_returns_created_run(client: TestClient) -> None:
    client.post("/api/v1/backtests", json=BODY)
    r = client.get("/api/v1/backtests", params={"limit": 10})
    assert len(r.json()["data"]) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && KEEL_TEST_PG_DSN="postgresql://keel:keel@127.0.0.1:5432/keel" UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_api_backtests.py -v`
Expected: FAIL —— 501

- [ ] **Step 3: 实现**

`backend/keel/api/routers/backtests.py` 的 `BacktestRequest` 与三个端点替换为：

```python
class BacktestRequest(BaseModel):
    strategy_key: str
    strategy_version: int = 1
    universe: list[str] = Field(min_length=1, max_length=100)
    interval: str
    start: str = Field(alias="from")
    end: str = Field(alias="to")
    params: dict = Field(default_factory=dict)
    cost_model_id: str
    engine: str = "keel-bar"

    model_config = {"populate_by_name": True}


@router.post("/backtests", status_code=201, response_model=Envelope[BacktestQueued])
def create_backtest(
    body: BacktestRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Envelope[BacktestQueued]:
    from keel.research import backtest as queue

    run_id = queue.enqueue(
        universe=body.universe,
        interval=body.interval,
        start=datetime.fromisoformat(body.start.replace("Z", "+00:00")),
        end=datetime.fromisoformat(body.end.replace("Z", "+00:00")),
        strategy_key=body.strategy_key,
        strategy_version=body.strategy_version,
        params=body.params,
        cost_model_id=body.cost_model_id,
        engine=body.engine,
        idempotency_key=idempotency_key,
    )
    return Envelope(data=BacktestQueued(run_id=run_id, status="QUEUED"), meta=Meta())


@router.get("/backtests/{run_id}")
def get_backtest(run_id: str) -> Envelope[dict]:
    from keel.research import backtest as queue

    run = queue.get(run_id)
    return Envelope(
        data={
            "run_id": run.run_id, "status": run.status.value,
            "fail_reason": run.fail_reason.value if run.fail_reason else None,
            "universe": run.universe, "interval": run.interval,
            "metrics": run.metrics, "stale": False,
        },
        meta=Meta(as_of=run.end_time),
    )


@router.get("/backtests")
def list_backtests(limit: int = 50, cursor: str | None = None) -> Envelope[list]:
    from keel.research import backtest as queue

    runs = queue.list_runs(limit=limit, cursor=cursor)
    return Envelope(
        data=[
            {"run_id": r.run_id, "status": r.status.value, "universe": r.universe,
             "interval": r.interval, "metrics": r.metrics}
            for r in runs
        ],
        meta=Meta(),
    )
```

`keel/research/backtest.py` 补：

```python
def list_runs(limit: int = 50, cursor: str | None = None) -> list[BacktestRun]:
    from keel.db.pool import connection

    with connection() as conn:
        rows = conn.execute(
            """select * from backtest_runs
                where (%s::text is null or created_at < (select created_at from backtest_runs
                                                          where run_id = %s))
                order by created_at desc limit %s""",
            (cursor, cursor, limit),
        ).fetchall()
    return [_to_run(r) for r in rows]
```

`backend/keel/api/routers/reports.py` 两个端点替换为：

```python
@router.get("/reports/{run_id}")
def get_report(run_id: str) -> Envelope[dict]:
    from keel.reports import artifacts

    artifact_id = artifacts.latest_for_run(run_id)
    if artifact_id is None:
        from keel.errors import NotFound

        raise NotFound(f"report not found for run: {run_id}")
    path = artifacts.resolve(artifact_id)
    import json

    return Envelope(
        data={"run_id": run_id, "report": json.loads(path.read_text()),
              "content_url": artifacts.content_url(artifact_id)},
        meta=Meta(),
    )


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> Envelope[dict]:
    from keel.reports import artifacts

    path = artifacts.resolve(artifact_id)
    return Envelope(
        data={
            "artifact_id": artifact_id,
            "bytes": path.stat().st_size,
            "content_url": artifacts.content_url(artifact_id),
        },
        meta=Meta(),
    )
```

`artifacts.latest_for_run(run_id: str) -> str | None` 从 PG `artifacts` 表按 `run_id` 取最新一条（无 PG 连接时返回 `None`）。

- [ ] **Step 4: 跑测试**

Run: `cd backend && KEEL_TEST_PG_DSN="postgresql://keel:keel@127.0.0.1:5432/keel" UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_api_backtests.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/api/routers backend/keel/research/backtest.py backend/tests/test_api_backtests.py
git commit -m "feat(api): 回测与报告接口 API-03/04/05/10/12"
```

---

## Task 18: Web 三页与图表

**Files:**
- Modify: `web/src/pages/Market.tsx`（真实 K 线）
- Modify: `web/src/pages/Backtests.tsx`（提交 + 列表）
- Create: `web/src/pages/Reports.tsx`
- Modify: `web/src/App.tsx`（加路由）
- Modify: `web/src/api/client.ts`（`fetchBars`/`submitBacktest`/`listBacktests`）
- Test: 手工验证（前端不引入测试框架，V1 阶段用 `npm run build` + 浏览器点）

**Interfaces:**
- Consumes: `/api/v1/bars`、`/api/v1/coverage`、`/api/v1/backtests`、`/api/v1/reports/{run_id}`
- Produces: 三个页面；`BarChart` 接受 `{open_time, open, high, low, close}[]`

- [ ] **Step 1: 扩 client**

`web/src/api/client.ts` 追加：

```ts
export type Bar = { open_time: string; open: number; high: number; low: number; close: number; volume: number };

export async function fetchBars(params: {
  symbol: string; interval: string; from: string; to: string; maxPoints?: number;
}): Promise<Bar[]> {
  const q = new URLSearchParams({
    symbol: params.symbol, interval: params.interval,
    from: params.from, to: params.to,
  });
  if (params.maxPoints) q.set("max_points", String(params.maxPoints));
  const body = await get<Bar[]>(`/bars?${q}`);
  return body;
}
```

（`get<T>` 用现有的 Envelope 解包函数，返回 `data`。）

- [ ] **Step 2: Market 页接真数据**

Market.tsx 用 `useEffect` 调 `fetchBars`，把结果喂给 `BarChart`；空数据时显示「该区间无数据」，并在 `meta.stale` 为 true 时渲染现有 `StaleBanner`。

- [ ] **Step 3: Backtests 页提交表单**

表单字段：`strategy_key`（下拉 `sma_cross`/`donchian_breakout`）、`interval`、`from`、`to`、`params`（JSON 文本框）、`cost_model_id`（默认 `default`）。提交后刷新列表，5 秒轮询一次直到状态不是 `QUEUED`/`RUNNING`。

- [ ] **Step 4: Reports 页**

选一条 `SUCCEEDED` 的 run → `GET /api/v1/reports/{run_id}` → 展示 `cagr`/`sharpe`/`max_drawdown`/`trade_count` 与 `disclaimer`（**必须显示**，A8）。

- [ ] **Step 5: 构建与手验**

```bash
cd web && npm install && npm run build && npx tsc --noEmit
cd /Users/wangwei/Documents/github/Keel/backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run uvicorn keel.api.app:app --host 127.0.0.1 --port 8000 &
cd ../web && npm run dev
```
Expected: `http://127.0.0.1:5173` 三页可点，K 线图有数据，报告页显示「模拟回测，非实盘业绩」。

- [ ] **Step 6: Commit**

```bash
git add web/src
git commit -m "feat(web): 行情、回测与报告三页接真数据"
```

---

## Task 19: 调度、心跳与告警

**Files:**
- Modify: `backend/keel/scheduler/jobs.py:40-41`
- Modify: `backend/keel/ops/heartbeat.py`
- Modify: `backend/keel/ops/alerts.py:34-42`
- Test: `backend/tests/test_ops.py`

**Interfaces:**
- Produces:
  - `keel.ops.heartbeat.beat(component: str) -> None`、`check(*, interval_seconds: int = 60, threshold: int = 3) -> list[str]`
  - `keel.ops.alerts.send(severity, alert_key, title, body) -> None`（`alert_key` 30 分钟内抑制）、`dead_man_switch(*, threshold: int = 3) -> None`
  - `keel.scheduler.jobs.register(scheduler) -> None`（注册 `JOBS` 里每个任务到 APScheduler）
  - `keel.scheduler.jobs.JOBS` 扩到 9 条，与文档 §11.1 调度表一致

- [ ] **Step 1: 写失败测试**

`backend/tests/test_ops.py`:

```python
"""心跳与告警抑制。"""

from __future__ import annotations

import time

import pytest

from keel.ops import alerts, heartbeat


@pytest.fixture(autouse=True)
def _dsn(pg_dsn: str, monkeypatch) -> None:
    from keel.db import migrations

    migrations.migrate(pg_dsn)
    monkeypatch.setenv("KEEL_PG_DSN", pg_dsn)
    from keel.config import get_settings
    from keel.db import pool

    get_settings.cache_clear()
    pool.reset_pool()
    import psycopg

    with psycopg.connect(pg_dsn) as conn:
        conn.execute("delete from heartbeats")
        conn.commit()


def test_beat_then_check_is_clean() -> None:
    heartbeat.beat("api")
    assert heartbeat.check() == []


def test_missing_component_is_reported() -> None:
    import psycopg

    heartbeat.beat("api")
    with psycopg.connect(__import__("os").environ["KEEL_PG_DSN"]) as conn:
        conn.execute(
            "insert into heartbeats (component, beat_at) values ('etl-worker', now() - interval '10 minutes')"
        )
        conn.commit()
    assert heartbeat.check(interval_seconds=60, threshold=3) == ["etl-worker"]


def test_alert_suppressed_within_window(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(alerts, "_deliver", lambda *a, **k: sent.append(a[0]))
    alerts.send(alerts.Severity.P1, "test-key", "t", "b")
    alerts.send(alerts.Severity.P1, "test-key", "t", "b")
    assert len(sent) == 1


def test_different_keys_are_not_suppressed(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(alerts, "_deliver", lambda *a, **k: sent.append(a[0]))
    alerts.send(alerts.Severity.P0, "k1", "t", "b")
    alerts.send(alerts.Severity.P0, "k2", "t", "b")
    assert len(sent) == 2


def test_jobs_cover_documented_schedule() -> None:
    from keel.scheduler.jobs import JOBS

    ids = {j.job_id for j in JOBS}
    assert {"daily-backfill", "build-marts", "coverage-check",
            "heartbeat-check", "archive-and-compress"} <= ids
    assert all("TZ" not in j.cron for j in JOBS)  # cron 表达式本身不带时区，注册时统一 UTC
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && KEEL_TEST_PG_DSN="postgresql://keel:keel@127.0.0.1:5432/keel" UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_ops.py -v`
Expected: FAIL —— `NotImplementedError: 心跳写入尚未实现`

- [ ] **Step 3: 实现**

`backend/keel/ops/heartbeat.py`:

```python
"""心跳。写库不写日志——容器挂了日志也就没了，而"容器挂了"正是要发现的场景。"""

from __future__ import annotations


def beat(component: str) -> None:
    from keel.db.pool import transaction

    with transaction() as conn:
        conn.execute(
            """insert into heartbeats (component, beat_at) values (%s, now())
               on conflict (component) do update set beat_at = now()""",
            (component,),
        )


def check(*, interval_seconds: int = 60, threshold: int = 3) -> list[str]:
    """返回失联组件清单。阈值按周期数算，不按绝对秒数——改心跳频率不必改告警。"""
    from keel.db.pool import connection

    with connection() as conn:
        rows = conn.execute(
            """select component from heartbeats
                where beat_at < now() - make_interval(secs => %s)
                order by component""",
            (interval_seconds * threshold,),
        ).fetchall()
    return [r["component"] for r in rows]
```

`backend/keel/ops/alerts.py` 的 `send`/`dead_man_switch` 替换为：

```python
_suppress: dict[str, float] = {}


def _deliver(title: str, body: str, severity: Severity) -> None:
    """真发送：Telegram 主、macOS 通知备。测试里被 monkeypatch 替换。"""
    import json
    import subprocess
    import urllib.request

    from keel.config import get_settings

    settings = get_settings()
    text = f"[{severity.value}] {title}\n{body}"
    if settings.alert_telegram_token and settings.alert_telegram_chat_id:
        payload = json.dumps(
            {"chat_id": settings.alert_telegram_chat_id, "text": text}
        ).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{settings.alert_telegram_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10)  # noqa: S310 — 固定域名
            return
        except Exception:  # 主通道挂了就走备通道，不能让告警本身成为故障点
            pass
    if settings.alert_local_notify:
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(body)} with title {json.dumps(title)}'], check=False)


def send(severity: Severity, alert_key: str, title: str, body: str) -> None:
    import time

    from keel.config import get_settings

    window = get_settings().alert_suppress_minutes * 60
    now = time.monotonic()
    last = _suppress.get(alert_key)
    if last is not None and now - last < window:
        return
    _suppress[alert_key] = now
    _deliver(title, body, severity)


def dead_man_switch(*, interval_seconds: int = 60, threshold: int = 3) -> None:
    from keel.ops import heartbeat

    missing = heartbeat.check(interval_seconds=interval_seconds, threshold=threshold)
    if missing:
        send(
            Severity.P0, "dead-man-switch", "组件失联",
            "以下组件超过 %d 个周期没有心跳：%s" % (threshold, ", ".join(missing)),
        )
```

`backend/keel/scheduler/jobs.py` 的 `JOBS` 补齐文档 §11.1 的 9 条（`sync-closed-bars`、`daily-backfill`、`build-marts`、`coverage-check`、`weekly-research-report`、`archive-and-compress`、`export-tx`、`heartbeat-check`、`feature-consistency-check`），`register` 实现为：

```python
def register(scheduler) -> None:
    """把 JOBS 注册到 APScheduler。时区固定 UTC，不读调度器默认值。"""
    from zoneinfo import ZoneInfo

    from keel.workers import scheduler_worker

    for job in JOBS:
        scheduler.add_job(
            scheduler_worker.dispatch,
            trigger="cron",
            kwargs={"job_id": job.job_id},
            id=job.job_id,
            replace_existing=True,
            timezone=ZoneInfo("UTC"),
            misfire_grace_time=300,
        )
```

`backend/keel/workers/scheduler_worker.py` 补 `dispatch(job_id: str) -> None`，按 `job_id` 分派到 `etl.run_once` / `backfill` / `alerts.dead_man_switch`，未知 `job_id` 记 warning。

- [ ] **Step 4: 跑测试**

Run: `cd backend && KEEL_TEST_PG_DSN="postgresql://keel:keel@127.0.0.1:5432/keel" UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_ops.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/keel/ops backend/keel/scheduler backend/keel/workers/scheduler_worker.py backend/tests/test_ops.py
git commit -m "feat(ops): 心跳、告警抑制与调度注册"
```

---

## Task 20: 端到端回填与验收脚本

**Files:**
- Create: `backend/keel/tools/__init__.py`
- Create: `backend/keel/tools/acceptance.py`
- Create: `backend/tests/test_acceptance.py`
- Modify: `Makefile`（`make backfill` / `make etl` / `make acceptance`）

**Interfaces:**
- Produces:
  - `keel.tools.acceptance.check_a1(root: Path, symbols: list[str], interval: str) -> dict`（`{"ratio": float, "blocking_gaps": int, "ok": bool}`）
  - `keel.tools.acceptance.check_a4(run_id: str, *, raw_root: Path) -> dict`（同 `run_id` 重跑两次比对 metrics，差异必须为 0）
  - `keel.tools.acceptance.main() -> int`（打印 A1–A10 结果，任一不达标返回非 0）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_acceptance.py`:

```python
"""验收检查项本身要能被测。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from keel.data.datasets import ODS_BARS
from keel.data.lake import partition_dir, write_partition
from keel.market.normalize import normalize_ohlcv
from keel.tools.acceptance import check_a1

HOUR = 3_600_000


def _seed(root: Path, gaps: list[int]) -> None:
    rows = []
    for i in range(10):
        if i in gaps:
            continue
        rows.append([1_699_200_000_000 + i * HOUR, 100.0, 101.0, 99.0, 100.0, 1.0])
    frame = normalize_ohlcv(rows, "BTC/USDT", "1h")
    write_partition(partition_dir(root, ODS_BARS, "BTC/USDT", "1h",
                                  datetime(2023, 11, 1, tzinfo=UTC)), frame)


def test_a1_fails_on_missing_bar(tmp_path: Path) -> None:
    _seed(tmp_path, gaps=[4])
    out = check_a1(tmp_path, ["BTC/USDT"], "1h")
    assert out["blocking_gaps"] == 0 and out["ratio"] < 1.0
    assert out["ok"] is False  # A1 要求覆盖率 ≥ 99.5% 且 blocking 为 0


def test_a1_passes_on_complete_data(tmp_path: Path) -> None:
    _seed(tmp_path, gaps=[])
    out = check_a1(tmp_path, ["BTC/USDT"], "1h")
    assert out["ok"] is True


def test_a1_flags_blocking_gap(tmp_path: Path) -> None:
    _seed(tmp_path, gaps=[4, 5, 6])
    out = check_a1(tmp_path, ["BTC/USDT"], "1h")
    assert out["blocking_gaps"] >= 1 and out["ok"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_acceptance.py -v`
Expected: FAIL —— `ModuleNotFoundError`

- [ ] **Step 3: 实现**

`backend/keel/tools/acceptance.py` 的核心（A1 与 A4 先做，其余按同一形状补）：

```python
"""把 §11.1 的 A1–A10 变成可执行检查。

验收标准写在文档里但没人跑过，就等于没有标准。这个脚本的每个函数都返回
``{"ok": bool, ...细节}``，``main`` 汇总后以退出码表达结果——能进 CI 的才是验收。
"""

from __future__ import annotations

from pathlib import Path

from keel.data.coverage import analyse
from keel.data.lake import read_bars

A1_RATIO_MIN = 0.995


def check_a1(root: Path, symbols: list[str], interval: str) -> dict:
    ratios = []
    blocking = 0
    for symbol in symbols:
        frame = read_bars(Path(root), symbol=symbol, interval=interval)
        cov = analyse(frame, interval)
        ratios.append(cov.ratio)
        blocking += cov.blocking_gaps
    ratio = sum(ratios) / len(ratios) if ratios else 0.0
    return {
        "name": "A1 数据覆盖率",
        "ratio": ratio,
        "blocking_gaps": blocking,
        "ok": ratio >= A1_RATIO_MIN and blocking == 0,
    }
```

`check_a4` 实现为：对同一 `run_id` 调两次 `runner.execute_run`，`json.dumps(metrics, sort_keys=True)` 必须完全相等。

- [ ] **Step 4: 跑测试**

Run: `cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest tests/test_acceptance.py -v`
Expected: 3 passed

- [ ] **Step 5: 真回填一批数据（首个端到端跑通点）**

```bash
cd backend
export KEEL_RAW_ROOT="$PWD/../data/raw" KEEL_DUCKDB_PATH="$PWD/../data/duckdb/keel.duckdb"
UV_CACHE_DIR="$PWD/../.uv-cache" uv run python -c "
from datetime import UTC, datetime
from pathlib import Path
from keel.market.backfill import BackfillJob, execute, plan
from keel.market.exchange import BinanceSpot
job = BackfillJob('BTC/USDT', '1h', datetime(2024,1,1,tzinfo=UTC), datetime(2024,4,1,tzinfo=UTC))
root = Path('../data/raw')
for todo in plan([job], root=root):
    print(execute(todo, exchange=BinanceSpot(), root=root))
"
```
Expected: 打印 `JobResult(rows_written≈2160, batches=3)`；再跑一次 plan 返回空

- [ ] **Step 6: Commit**

```bash
git add backend/keel/tools backend/tests/test_acceptance.py Makefile
git commit -m "feat(tools): 验收检查脚本与端到端回填"
```

---

## Task 21: 文档同步与 CI 收口

**Files:**
- Modify: `docs/architecture-design.md`（§11.1 A3 表述、§5.4 `ops_coverage` 派生、附录 C 加 ADR-008/ADR-009、附录 B v0.8 行）
- Modify: `.github/workflows/ci.yml`（加 postgres service 与 `KEEL_TEST_PG_DSN`）
- Modify: `README.md`（V1 跑法：起库 → 回填 → ETL → 回测 → 看页面）

**Interfaces:**
- Consumes: 前 20 个 task 的全部产物
- Produces: 文档与代码一致；CI 跑全部测试

- [ ] **Step 1: 改文档**

- §5.4 `ops_coverage` 描述改为「由 ETL 从原始湖派生，回填进程不写它」。
- §11.1 A3 改为「`dws_features` 与 `keel.features.compute` 现算值逐值一致（1e-9）；`keel-bar` 与 `dws_features` 两处口径」。
- 附录 C 加 `ADR-008 ops_coverage 是派生表`、`ADR-009 V1 用 keel-bar 替代 Freqtrade`。
- 附录 B 加 v0.8 行与「v0.8 未做的事」（Freqtrade 适配、永续、多标的组合权重、`cost_models` 从 PG 读）。

- [ ] **Step 2: 改 CI**

`.github/workflows/ci.yml` 的 backend job 加：

```yaml
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: keel
          POSTGRES_PASSWORD: keel
          POSTGRES_DB: keel
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U keel -d keel"
          --health-interval 5s --health-timeout 3s --health-retries 10
    env:
      KEEL_TEST_PG_DSN: postgresql://keel:keel@127.0.0.1:5432/keel
```

- [ ] **Step 3: 本地全量验证**

```bash
cd backend
export KEEL_TEST_PG_DSN="postgresql://keel:keel@127.0.0.1:5432/keel"
UV_CACHE_DIR="$PWD/../.uv-cache" uv run pytest -q
UV_CACHE_DIR="$PWD/../.uv-cache" uv run ruff check . && UV_CACHE_DIR="$PWD/../.uv-cache" uv run ruff format --check .
cd ../web && npx tsc --noEmit && npm run build
```
Expected: pytest 无失败、ruff 干净、前端构建成功

- [ ] **Step 4: 跑验收**

```bash
cd backend && UV_CACHE_DIR="$PWD/../.uv-cache" uv run python -m keel.tools.acceptance --symbols BTC/USDT,ETH/USDT --interval 1h
```
Expected: 打印 A1–A10，A9/A3/A4 必须 ok；A6 需要服务跑起来，脚本里对不可达的服务标 `skipped` 而不是 `ok`

- [ ] **Step 5: Commit**

```bash
git add docs/architecture-design.md .github/workflows/ci.yml README.md Makefile
git commit -m "docs: v0.8 同步 V1 实现与验收口径"
```

---

## Self-Review

**规格覆盖**

| 规格 | 落在 |
| --- | --- |
| A1 覆盖率 | Task 6（分级）+ Task 20（`check_a1`） |
| A2 回填幂等 | Task 7（`plan` 裁剪 + 分区只追加 + 重跑测试） |
| A3 特征一致 1e-9 | Task 9（ETL 写 `dws_features`）+ Task 21（口径改写；实现见 `test_etl` 与 `test_contract` 的无未来函数断言） |
| A4 同 run 重跑零差异 | Task 20（`check_a4`） |
| A5 引擎差异记录 | 计划 D2 把归因移到 V2；V1 只保留 `metrics` 与 `trades` 落库（Task 13/14） |
| A6 延迟 p95 | Task 16/17 的接口 + Task 21 脚本里的计时检查 |
| A7 无私有 Key、网关未启动 | 骨架已满足（`execution/gateway.py` 仍 `NotImplementedError`），Task 21 的 CI 静态扫描守着 |
| A8 报告标「模拟回测」 | Task 15（`disclaimer` 字段）+ Task 18（页面显示） |
| A9 契约冻结 | 已达成（`tests/test_contract.py` 40 passed），Task 10 只加类属性不动签名 |
| A10 从零恢复 | Task 1（`migrate`）+ Task 9（可从 Parquet 重建集市）+ Task 21 的 README 步骤 |

**类型一致性检查**

- `CostModel` 字段（`fee_rate`/`slippage_bps`/`min_notional`/`funding_included`）在 Task 11/14 使用一致。
- `Simulation.equity` 是 `pd.Series`，`compute_metrics(equity, trades, interval)` 三处调用同一签名。
- `BacktestRun` 在 Task 13 定义、Task 14 消费，字段名 `universe`/`strategy_key`/`strategy_version`/`cost_model_id`/`engine` 一致。
- `mart.publish(build, target)` 在 Task 3 定义、Task 9/16 调用，签名一致。
- `interval_to_ms` 在 Task 4 定义、Task 6/7/16 使用，`Interval` 枚举（`keel.reports.quantstats`）与字符串混用处已用 `str` 参数统一。

**未做的事（明确写出，避免被当成遗漏）**

- Freqtrade 与 VectorBT 适配（D2，V1.5）。
- 永续、杠杆、资金费（ADR-001，V3）。
- 多标的组合权重与再平衡（V3）。
- `cost_models` 从 PG 读取（V1 用内置默认常量，Task 14 注明）。
- 回测优先级队列（附录 B 已列为未做项）。
