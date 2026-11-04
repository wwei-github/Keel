"""告警。§2.5.7。

分级决定打扰程度，不是决定"要不要看"：
- P0 立即推（订单未知状态、对账差异、密钥失效、总开关触发）
- P1 5 分钟内汇总推
- P2 每日汇总

主通道 Telegram、备通道 macOS 本地通知（``osascript``）。
同 ``alert_key`` 30 分钟内抑制——连发 200 条一样的告警等于没有告警。
"""

from __future__ import annotations

import time
from enum import StrEnum


class Severity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


# 抑制窗口放进程内存：重启后丢一次抑制只会多发一条，不会漏发。
# 需要跨进程共享时再挪到 PG 的 alert_log 表。
_suppress: dict[str, float] = {}


def _deliver(title: str, body: str, severity: Severity) -> None:
    """真发送：Telegram 主、macOS 通知备。测试里被 monkeypatch 替换。"""
    import json
    import subprocess
    import urllib.request

    from keel.config import get_settings

    settings = get_settings()
    text = f"[{severity.value}] {title}\n{body}"
    if settings.alert_telegram_token and settings.alert_telegram_chat_id:
        payload = json.dumps({"chat_id": settings.alert_telegram_chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{settings.alert_telegram_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10)  # noqa: S310 — 固定域名
            return
        except Exception:  # noqa: BLE001 — 主通道挂了就走备通道，告警不能自己成为故障点
            pass
    if settings.alert_local_notify:
        subprocess.run(  # noqa: S603,S607 — 固定命令、固定参数形状
            [
                "osascript",
                "-e",
                f"display notification {json.dumps(body)} with title {json.dumps(title)}",
            ],
            check=False,
        )


def send(severity: Severity, alert_key: str, title: str, body: str) -> None:
    """按分级发送，并按 ``alert_key`` 做抑制。

    抑制键是调用方给的语义键（如 ``recon_diff:BTCUSDT``），不是标题——
    标题会随数值变化（"差异 3 笔" 与 "差异 5 笔" 是两个不同的字符串），
    拿标题当键等于完全不做抑制。
    """
    from keel.config import get_settings

    window = get_settings().alert_suppress_minutes * 60
    now = time.monotonic()
    last = _suppress.get(alert_key)
    if last is not None and now - last < window:
        return
    _suppress[alert_key] = now
    _deliver(title, body, severity)


def dead_man_switch(*, interval_seconds: int = 60, threshold: int = 3) -> None:
    """心跳缺失 3 个周期时触发。它本身是最重要的告警：系统静默 ≠ 系统正常。"""
    from keel.ops import heartbeat

    missing = heartbeat.check(interval_seconds=interval_seconds, threshold=threshold)
    if missing:
        send(
            Severity.P0,
            "dead-man-switch",
            "组件失联",
            f"以下组件超过 {threshold} 个周期没有心跳：{', '.join(missing)}",
        )
