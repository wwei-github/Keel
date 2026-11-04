"""配置加载。

一个原则：**配置只有一份来源**。环境变量能覆盖的值，都在 `Settings` 里声明默认值，
不允许在别处再写一遍 ``os.environ.get(...)`` 兜底——那样两处默认值迟早分叉。

密钥不进配置文件，只从环境变量读，且不参与任何日志输出。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """进程启动时一次性读取，之后不可变。"""

    env: str = "local"
    timezone: str = "UTC"

    # PostgreSQL：状态库 + 事件队列（§2.5.2）
    pg_dsn: str = "postgresql://keel:keel@localhost:5432/keel"
    pg_pool_min: int = 1
    pg_pool_max: int = 5

    # DuckDB：单写者，所以这里只有路径，没有并发参数（§3.3）
    duckdb_path: str = "data/duckdb/keel.duckdb"
    duckdb_memory_limit: str = "2GB"

    # 原始湖与制品库
    raw_root: str = "data/raw"
    artifact_root: str = "data/artifacts"

    # 服务 API（§6.1）
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_token: str = ""
    api_rate_limit_control_per_min: int = 10

    # 交易所（§0.2 H2：V1–V2 只有 Binance 现货）
    exchange_id: str = "binance"
    exchange_market_type: str = "spot"
    exchange_api_key: str = ""
    exchange_api_secret: str = ""

    # 告警（§2.5.7）
    alert_telegram_token: str = ""
    alert_telegram_chat_id: str = ""
    alert_local_notify: bool = True
    alert_suppress_minutes: int = 30

    # 回测执行器（§2.5.6）。并发默认 1：回测内存与 CPU 密集，
    # 并行只会互相拖慢并触发 OOM。
    backtest_concurrency: int = 1
    backtest_timeout_seconds: int = 1800

    # 运维
    log_level: str = "INFO"
    log_json: bool = True
    heartbeat_interval_seconds: int = 60
    heartbeat_miss_threshold: int = 3

    extra: dict[str, str] = field(default_factory=dict)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。测试用 ``get_settings.cache_clear()`` 重置。"""
    return Settings(
        env=os.environ.get("KEEL_ENV", "local"),
        pg_dsn=os.environ.get("KEEL_PG_DSN", "postgresql://keel:keel@localhost:5432/keel"),
        duckdb_path=os.environ.get("KEEL_DUCKDB_PATH", "data/duckdb/keel.duckdb"),
        raw_root=os.environ.get("KEEL_RAW_ROOT", "data/raw"),
        artifact_root=os.environ.get("KEEL_ARTIFACT_ROOT", "data/artifacts"),
        api_host=os.environ.get("KEEL_API_HOST", "127.0.0.1"),
        api_port=int(os.environ.get("KEEL_API_PORT", "8000")),
        api_token=os.environ.get("KEEL_API_TOKEN", ""),
        exchange_id=os.environ.get("KEEL_EXCHANGE_ID", "binance"),
        exchange_api_key=os.environ.get("KEEL_EXCHANGE_API_KEY", ""),
        exchange_api_secret=os.environ.get("KEEL_EXCHANGE_API_SECRET", ""),
        alert_telegram_token=os.environ.get("KEEL_ALERT_TELEGRAM_TOKEN", ""),
        alert_telegram_chat_id=os.environ.get("KEEL_ALERT_TELEGRAM_CHAT_ID", ""),
        alert_local_notify=_bool("KEEL_ALERT_LOCAL_NOTIFY", True),
        backtest_concurrency=int(os.environ.get("KEEL_BACKTEST_CONCURRENCY", "1")),
        backtest_timeout_seconds=int(os.environ.get("KEEL_BACKTEST_TIMEOUT_SECONDS", "1800")),
        log_level=os.environ.get("KEEL_LOG_LEVEL", "INFO"),
        log_json=_bool("KEEL_LOG_JSON", True),
    )
