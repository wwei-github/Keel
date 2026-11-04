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
