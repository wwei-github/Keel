"""实时采集（V2，Cryptofeed WebSocket）。§3.1。

职责单一：不丢数据。它不算指标、不做决策、不阻塞在策略上。

"行情触发源但异步解耦"是这个模块唯一要守住的语义：
策略进程挂了，采集照常写盘；采集挂了，策略收不到新 bar 就不动作，
由心跳告警兜底，绝不自己轮询交易所。
"""

from __future__ import annotations


def start() -> None:
    """启动 WS 订阅循环（Cryptofeed）。"""
    raise NotImplementedError("采集器尚未实现：V1 只需回补，V2 才需要实时")
