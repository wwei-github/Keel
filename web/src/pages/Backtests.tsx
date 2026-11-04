import { useState } from 'react'
import { api } from '../api/client'
import { NotImplementedNotice } from '../components/NotImplemented'

/**
 * 回测页。注意这是"提交"页而不是"运行"页：
 * 提交后拿到 `run_id` 就返回，执行由 backtest-worker 认领（§2.5.6）。
 * 页面应该显示排队状态，而不是转圈等到出结果。
 */
export default function Backtests() {
  const [runId, setRunId] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const key = crypto.randomUUID()
      const r = await api.post<{ run_id: string; status: string }>(
        '/backtests',
        {
          strategy_id: 'sma_cross',
          symbol: 'BTC/USDT',
          interval: '1h',
          start: '2024-01-01T00:00:00Z',
          end: '2025-01-01T00:00:00Z',
          params: { fast: 20, slow: 60 },
          cost_model_id: 'binance_spot_default',
        },
        key,
      )
      setRunId(r.data.run_id)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>回测</h2>
      <div className="card">
        <p className="muted" style={{ marginTop: 0 }}>
          提交后立即返回 run_id，执行由独立 worker 认领——不在 API 进程里跑回测。
        </p>
        <button onClick={submit} disabled={busy}>
          {busy ? '提交中…' : '提交 SMA 双均线回测'}
        </button>
        {runId ? <p className="muted">已入队：{runId}</p> : null}
      </div>
      {error ? (
        <div style={{ marginTop: 16 }}>
          <NotImplementedNotice error={error} />
        </div>
      ) : null}
    </div>
  )
}
