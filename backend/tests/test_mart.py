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
    with (
        mart.writer_lock(tmp_path),
        pytest.raises(mart.MartBusy, match="writer"),
        mart.writer_lock(tmp_path),
    ):
        pass


def test_writer_lock_released_after_block(tmp_path: Path) -> None:
    with mart.writer_lock(tmp_path):
        pass
    with mart.writer_lock(tmp_path):
        pass


def test_connect_missing_mart_raises(tmp_path: Path) -> None:
    with (
        pytest.raises(mart.MartMissing, match="keel.duckdb"),
        mart.connect(tmp_path / "keel.duckdb"),
    ):
        pass


def test_reader_cannot_write(tmp_path: Path) -> None:
    target = tmp_path / "keel.duckdb"
    mart.publish(_build, target)
    with mart.connect(target) as con, pytest.raises(duckdb.Error):
        con.execute("create table nope as select 1")
