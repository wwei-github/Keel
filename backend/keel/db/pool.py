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
