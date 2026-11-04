"""回测引擎适配层。

两套引擎，用途不同（ADR-003）：
- VectorBT：向量化，快一到两个数量级，用于参数粗筛。
- Freqtrade：逐 bar 事件驱动，慢但贴近实盘，用于最终验证。

A3 只要求 ``dws_features`` 与 Freqtrade 路径一致——VectorBT 的成本模型本就是桶化的，
要求它和逐笔引擎逐值相等是在要求两套模型变成同一套模型。
"""

from __future__ import annotations

from enum import StrEnum


class Engine(StrEnum):
    VECTORBT = "vectorbt"
    FREQTRADE = "freqtrade"


def run(engine: Engine, request: dict) -> dict:
    raise NotImplementedError("引擎适配尚未实现")
