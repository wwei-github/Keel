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
