"""特征集市入口。§2.5.5、§3.7。

这个模块**只是转发**，实际计算在 ``keel.features.compute``。转发层存在的理由：
ETL 与研究代码不该直接 import 特征实现，否则将来换实现要多处改。

特征版本是契约的一部分：``dws_features`` 主键含 ``feature_version``，
回测结果也记 ``feature_version``——否则没法回答"这条曲线是用哪版特征算的"。
"""

from __future__ import annotations

import pandas as pd

from keel.features import FEATURE_VERSION, compute


def compute_batch(bars: pd.DataFrame, feature_version: str = FEATURE_VERSION) -> pd.DataFrame:
    """对一批 bar 算特征。委托给 ``keel.features.compute``，不重复实现。"""
    return compute(bars, feature_version)
