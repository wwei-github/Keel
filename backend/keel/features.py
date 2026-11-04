"""特征入口（对应架构文档 §3.7「特征入口」契约项）。

铁律：**策略内禁止自行计算指标**，一律调用本模块。

理由（文档 §2.5.5）：回测、VectorBT 筛选、实盘三处必须用同一份计算逻辑，
否则 A3「指标逐值一致」无从谈起。策略自己写一遍 ``rolling(20).mean()``
看起来无害，但它与 ETL 的实现会以「浮点求和顺序」这种极隐蔽的方式分叉。

本模块分两层：

- **原语层**（``sma`` / ``donchian`` / ``atr`` / ``roc``）：参数的单一实现。
  策略需要「20 日均线」时调 ``sma(close, 20)``，而不是自己 ``rolling``。
- **固定特征集**（``compute``）：ETL 落 ``dws_features`` 用的那一组，
  由原语组合而成，保证「策略里算的」与「表里存的」是同一份代码。

接口形状固定：无全局状态、无 I/O、无时间依赖。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import pandas as pd

__all__ = [
    "FEATURE_VERSION",
    "SUPPORTED_VERSIONS",
    "UnknownFeatureVersion",
    "sma",
    "donchian",
    "atr",
    "roc",
    "compute",
    "feature_dtypes",
]

#: 当前特征定义版本。任何会改变输出数值的改动都必须改这个字符串，
#: 否则历史回测的 ``feature_version`` 会说谎。
FEATURE_VERSION: Final[str] = "feat-v1"

SUPPORTED_VERSIONS: Final[frozenset[str]] = frozenset({FEATURE_VERSION})

REQUIRED_COLUMNS: Final[tuple[str, ...]] = ("open", "high", "low", "close", "volume")


class UnknownFeatureVersion(ValueError):
    """``feature_version`` 不在支持列表内。不允许「猜一个最接近的版本」。"""


# --------------------------------------------------------------------------
# 原语层：所有指标的唯一实现
# --------------------------------------------------------------------------


def sma(close: pd.Series, n: int) -> pd.Series:
    """简单移动平均。窗口不足处为 NaN（不填充）。"""
    if n < 1:
        raise ValueError(f"n 必须 >= 1，收到 {n}")
    return close.astype("float64").rolling(n, min_periods=n).mean()


def donchian(high: pd.Series, low: pd.Series, n: int) -> tuple[pd.Series, pd.Series]:
    """唐奇安通道 ``(上轨, 下轨)``，**已 shift(1)**。

    那个 ``shift(1)`` 是关键：当前 bar 的最高价不能进入「过去 n 根的最高价」，
    否则突破判断会退化成「当前 bar 一定在自己的高点附近」，信号全是噪声。
    这个偏移放在原语里而不是调用方，是为了让「忘了 shift」这种错误不可能发生。
    """
    if n < 1:
        raise ValueError(f"n 必须 >= 1，收到 {n}")
    upper = high.astype("float64").rolling(n, min_periods=n).max().shift(1)
    lower = low.astype("float64").rolling(n, min_periods=n).min().shift(1)
    return upper, lower


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    """平均真实波幅。``close`` 用于计算跳空幅度。"""
    if n < 1:
        raise ValueError(f"n 必须 >= 1，收到 {n}")
    h = high.astype("float64")
    lo = low.astype("float64")
    c = close.astype("float64")
    prev_close = c.shift(1)
    true_range = pd.concat([h - lo, (h - prev_close).abs(), (lo - prev_close).abs()], axis=1).max(
        axis=1
    )
    return true_range.rolling(n, min_periods=n).mean()


def roc(close: pd.Series, n: int) -> pd.Series:
    """变动率 ``close / close.shift(n) - 1``。"""
    if n < 1:
        raise ValueError(f"n 必须 >= 1，收到 {n}")
    c = close.astype("float64")
    return c / c.shift(n) - 1.0


# --------------------------------------------------------------------------
# 固定特征集：ETL 落 dws_features 的那一组
# --------------------------------------------------------------------------


def compute(bars: pd.DataFrame, feature_version: str = FEATURE_VERSION) -> pd.DataFrame:
    """由 K 线计算固定特征集。

    参数
    ----
    bars
        索引为带时区的时间，列含 open/high/low/close/volume。必须是**已收盘**的 bar。
    feature_version
        特征定义版本。未知版本抛异常，不做降级。

    返回
    ----
    与 ``bars`` 同索引的 DataFrame，列：
    ``sma_20``、``sma_60``、``donchian_high_20``、``donchian_low_20``、``atr_14``、``roc_1``。
    窗口不足处保留 ``NaN`` —— 「不足」和「是 0」完全不同，
    填 0 会让策略在冷启动阶段产生虚假信号。
    """
    if feature_version not in SUPPORTED_VERSIONS:
        raise UnknownFeatureVersion(
            f"未知 feature_version={feature_version!r}，支持 {sorted(SUPPORTED_VERSIONS)}"
        )

    missing = [c for c in REQUIRED_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"bars 缺少列：{missing}")

    index = bars.index
    close, high, low = bars["close"], bars["high"], bars["low"]

    upper, lower = donchian(high, low, 20)

    out = pd.DataFrame(index=index)
    out["sma_20"] = sma(close, 20)
    out["sma_60"] = sma(close, 60)
    out["donchian_high_20"] = upper
    out["donchian_low_20"] = lower
    out["atr_14"] = atr(high, low, close, 14)
    out["roc_1"] = roc(close, 1)
    return out


def feature_dtypes() -> Mapping[str, str]:
    """特征列与 dtype 的对照，供 schema 校验与文档生成用。"""
    return {
        "sma_20": "float64",
        "sma_60": "float64",
        "donchian_high_20": "float64",
        "donchian_low_20": "float64",
        "atr_14": "float64",
        "roc_1": "float64",
    }
