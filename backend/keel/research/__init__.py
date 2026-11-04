"""研究实验室：回测执行与参数扫描。见 §3.7、§2.5.6。

本包由 `backtest-worker` 进程调用，不由 API 进程调用。
API 只入队，worker 用 `FOR UPDATE SKIP LOCKED` 认领——这条分工要守住，
因为它是"API 卡死"最常见的成因。
"""
