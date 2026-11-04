import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { ErrorNotice } from '../components/NotImplemented'
import { num, utc } from '../api/format'

interface RunSummary {
  run_id: string
  status: string
  universe: string[]
  interval: string
  metrics: Record<string, number> | null
}

interface RunDetail extends RunSummary {
  fail_reason: string | null
  stale: boolean
}

interface Report {
  run_id: string
  report: {
    metrics: Record<string, number>
    disclaimer: string
    start: string
    end: string
  }
  content_url: string
}

const METRICS: { key: string; label: string; pct?: boolean }[] = [
  { key: 'total_return', label: '累计收益', pct: true },
  { key: 'sharpe', label: '夏普' },
  { key: 'max_drawdown', label: '最大回撤', pct: true },
  { key: 'turnover', label: '换手' },
  { key: 'win_rate', label: '胜率', pct: true },
  { key: 'trade_count', label: '成交笔数' },
]

/**
 * 回测页：提交 + 列表 + 详情。
 *
 * 提交后**立刻**拿到 run_id，执行由 backtest-worker 认领（§2.5.6）。
 * 所以页面必须是"排队中"而不是转圈等结果——等结果的页面会诱导人把回测塞进 API 进程。
 */
export default function Backtests() {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const loadRuns = useCallback(async () => {
    try {
      const r = await api.get<RunSummary[]>('/backtests', { limit: 50 })
      setRuns(r.data)
    } catch (e) {
      setError(e)
    }
  }, [])

  useEffect(() => {
    void loadRuns()
  }, [loadRuns])

  async function submit() {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const r = await api.post<{ run_id: string; status: string }>(
        '/backtests',
        {
          strategy_key: 'sma_cross',
          strategy_version: 1,
          universe: ['BTC/USDT'],
          interval: '1h',
          from: '2024-01-02T00:00:00Z',
          to: '2024-01-14T00:00:00Z',
          params: { fast: 20, slow: 60 },
          cost_model_id: 'default',
          engine: 'keel-bar',
        },
        crypto.randomUUID(),
      )
      setNotice(`已入队 ${r.data.run_id}（${r.data.status}）——执行由 backtest-worker 认领`)
      await loadRuns()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  async function open(runId: string) {
    setSelected(runId)
    setReport(null)
    setError(null)
    try {
      const r = await api.get<RunDetail>(`/backtests/${runId}`)
      setDetail(r.data)
      if (r.data.status === 'SUCCEEDED') {
        const rep = await api.get<Report>(`/reports/${runId}`)
        setReport(rep.data)
      }
    } catch (e) {
      setError(e)
      setDetail(null)
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>回测</h2>

      <div className="card" style={{ marginBottom: 16 }}>
        <p className="muted" style={{ marginTop: 0 }}>
          提交只入队，立即返回 run_id。执行在独立的 <code>backtest-worker</code> 里跑，
          30 分钟超时、并发 1——回测是内存密集型，并行只会互相拖慢。
        </p>
        <button onClick={() => void submit()} disabled={busy}>
          {busy ? '提交中…' : '提交 SMA 双均线回测'}
        </button>
        {notice ? <p className="muted">{notice}</p> : null}
      </div>

      {error ? <ErrorNotice error={error} /> : null}

      <div className="card" style={{ marginBottom: 16 }}>
        <table>
          <thead>
            <tr>
              <th>run_id</th>
              <th>状态</th>
              <th>标的</th>
              <th>周期</th>
              <th>累计收益</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 ? (
              <tr>
                <td colSpan={6} className="muted">
                  还没有回测记录。
                </td>
              </tr>
            ) : (
              runs.map((r) => (
                <tr key={r.run_id}>
                  <td>
                    <code>{r.run_id}</code>
                  </td>
                  <td>
                    <span className={r.status === 'SUCCEEDED' ? 'badge' : 'badge warn'}>
                      {r.status}
                    </span>
                  </td>
                  <td>{(r.universe ?? []).join(', ')}</td>
                  <td>{r.interval}</td>
                  <td>
                    {r.metrics?.total_return !== undefined
                      ? `${(r.metrics.total_return * 100).toFixed(2)}%`
                      : '—'}
                  </td>
                  <td>
                    <button onClick={() => void open(r.run_id)} disabled={r.run_id === selected}>
                      详情
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {detail ? (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>
            <code>{detail.run_id}</code>
          </h3>
          {detail.stale ? (
            <p>
              <span className="badge warn">数据已修订</span>{' '}
              <span className="muted">该结果基于旧快照，不自动重跑——需要人决定是否重算。</span>
            </p>
          ) : null}
          {detail.fail_reason ? (
            <p>
              <span className="badge error">{detail.fail_reason}</span>
            </p>
          ) : null}
          {report ? (
            <>
              <table>
                <tbody>
                  {METRICS.map((m) => {
                    const v = report.report.metrics[m.key]
                    return (
                      <tr key={m.key}>
                        <th style={{ width: 140 }}>{m.label}</th>
                        <td>
                          {v === undefined
                            ? '—'
                            : m.pct
                              ? `${(v * 100).toFixed(2)}%`
                              : num(v, 4)}
                        </td>
                      </tr>
                    )
                  })}
                  <tr>
                    <th>区间</th>
                    <td className="muted">
                      {utc(report.report.start)} → {utc(report.report.end)}
                    </td>
                  </tr>
                  <tr>
                    <th>制品</th>
                    <td>
                      <a href={report.content_url} target="_blank" rel="noreferrer">
                        {report.content_url}
                      </a>
                    </td>
                  </tr>
                </tbody>
              </table>
              {/* A8：报告必须自带这句。由后端写进制品，前端原样展示，不硬编码。 */}
              <p className="muted" style={{ marginBottom: 0 }}>
                {report.report.disclaimer}
              </p>
            </>
          ) : (
            <p className="muted">
              {detail.status === 'SUCCEEDED' ? '报告尚未生成。' : `当前状态：${detail.status}`}
            </p>
          )}
        </div>
      ) : null}
    </div>
  )
}
