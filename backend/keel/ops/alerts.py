"""告警。§2.5.7。

分级决定打扰程度，不是决定"要不要看"：
- P0 立即推（订单未知状态、对账差异、密钥失效、总开关触发）
- P1 5 分钟内汇总推
- P2 每日汇总

主通道 Telegram、备通道 macOS 本地通知（``osascript``）。
同 ``alert_key`` 30 分钟内抑制——连发 200 条一样的告警等于没有告警。
"""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


def send(severity: Severity, alert_key: str, title: str, body: str) -> None:
    """按分级发送，并按 ``alert_key`` 做抑制。"""
    raise NotImplementedError("告警发送尚未实现：等待 Telegram 客户端")


def dead_man_switch() -> None:
    """心跳缺失 3 个周期时触发。它本身是最重要的告警：系统静默 ≠ 系统正常。"""
    raise NotImplementedError("dead-man switch 尚未实现")
