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
