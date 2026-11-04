"""最小迁移器。

不引入 Alembic：单人单机、单库、没有并行分支，一个按文件名排序的 SQL 目录够了。
引入迁移框架的成本在于它的抽象，而这里没有任何需要被抽象的东西。

迁移必须可重入：``CREATE TABLE IF NOT EXISTS`` 之类，重复跑不报错。
"""

from __future__ import annotations

from pathlib import Path

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def sql() -> str:
    return SCHEMA_FILE.read_text(encoding="utf-8")


def migrate(dsn: str) -> None:
    """对目标库执行 schema。幂等。"""
    raise NotImplementedError("迁移执行尚未实现：等待 psycopg 连接层")
