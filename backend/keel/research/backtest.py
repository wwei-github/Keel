"""回测作业的认领与状态机。§2.5.6。

状态机：QUEUED → RUNNING → SUCCEEDED / FAILED(REASON)。

三个容易做错的点：
- 认领用 ``SELECT ... FOR UPDATE SKIP LOCKED``，否则多个 worker 会抢同一条。
- 超时置 ``FAILED(TIMEOUT)`` 且**不自动重试**：超时通常意味着参数或数据有问题，
  自动重试只是把同一个失败重演一遍。
- worker 启动时把 ``RUNNING`` 超过 2 倍 timeout 的任务置 ``FAILED(ORPHANED)``，
  否则进程被 kill 的任务会永远卡在 RUNNING。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class FailReason(StrEnum):
    TIMEOUT = "TIMEOUT"
    ORPHANED = "ORPHANED"
    ERROR = "ERROR"


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


def _to_run(row: dict) -> BacktestRun:
    """psycopg 的 dict_row 已经把 jsonb 解成 dict/list，这里只做枚举转换。"""
    reason = row.get("fail_reason")
    return BacktestRun(
        run_id=row["run_id"],
        status=RunStatus(row["status"]),
        universe=list(row.get("universe") or []),
        interval=row["interval"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        strategy_key=row["strategy_key"],
        strategy_version=int(row["strategy_version"]),
        params=dict(row.get("params") or {}),
        cost_model_id=row["cost_model_id"],
        engine=row["engine"],
        fail_reason=FailReason(reason) if reason else None,
        metrics=row.get("metrics"),
    )


def enqueue(**kw) -> str:
    """写一条 QUEUED 并返回 run_id。同一 Idempotency-Key 复用已有 run。"""
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
                run_id,
                kw["universe"][0],
                kw["interval"],
                kw["start"],
                kw["end"],
                json.dumps(kw["universe"]),
                kw["strategy_key"],
                kw["strategy_version"],
                json.dumps(kw.get("params") or {}),
                kw["cost_model_id"],
                kw.get("engine") or "keel-bar",
                kw.get("data_snapshot_id"),
                key,
            ),
        )
    return run_id


def get(run_id: str) -> BacktestRun:
    from keel.db.pool import connection
    from keel.errors import NotFound

    with connection() as conn:
        row = conn.execute("select * from backtest_runs where run_id = %s", (run_id,)).fetchone()
    if row is None:
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
    from keel.db.pool import transaction

    status = "FAILED" if error else "SUCCEEDED"
    with transaction() as conn:
        conn.execute(
            """update backtest_runs
                  set status = %s, finished_at = now(), metrics = %s::jsonb,
                      error = %s::text,
                      fail_reason = case when %s::text is null then null else 'ERROR' end
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


def list_runs(*, limit: int = 50, cursor: str | None = None) -> list[BacktestRun]:
    """按创建时间倒序。游标是上一页最后一条的 run_id（keyset 分页）。"""
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
