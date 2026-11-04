"""策略运行时（V2，Freqtrade 承载）。见 §3.4。

契约在 `keel.contracts`，特征在 `keel.features`，本包负责把两者接起来：
按 `lookback` 预取、逐 bar 调用 `run_decide`、把 `state` 持久化与回放。
"""
