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

interface ReportPayload {
  run_id: string
  report: {
    metrics: Record<string, number>
    disclaimer: string
    start: string
    end: string
    strategy_key: string
    engine: string
  }
  content_url: string
}

/**
 * 报告索引：列出所有跑成功的回测，点开看指标。
 *
 * 只列 SUCCEEDED：报告是「跑完了的产物」，把排队中和失败的混进来，
 * 这个页面就会变成回测列表的重复，而不是「可引用结果」的清单。
 */
export default function Reports() {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [payload, setPayload] = useState<ReportPayload | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(async () => {
    try {
      const r = await api.get<RunSummary[]>('/backtests', { limit: 50 })
      setRuns(r.data.filter((x) => x.status === 'SUCCEEDED'))
    } catch (e) {
      setError(e)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function view(runId: string) {
    setOpen(runId)
    setPayload(null)
    setError(null)
    try {
      const r = await api.get<ReportPayload>(`/reports/${runId}`)
      setPayload(r.data)
    } catch (e) {
      setError(e)
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>报告</h2>
      <p className="muted">
        每份报告是一个制品（JSON），接口只返回数据和 <code>content_url</code>，
        不返回服务器路径——换存储不该破坏接口。
      </p>

      {error ? <ErrorNotice error={error} /> : null}

      <div className="card" style={{ marginBottom: 16 }}>
        <table>
          <thead>
            <tr>
              <th>run_id</th>
              <th>标的</th>
              <th>周期</th>
              <th>累计收益</th>
              <th>夏普</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 ? (
              <tr>
                <td colSpan={6} className="muted">
                  还没有成功的回测。
                </td>
              </tr>
            ) : (
              runs.map((r) => (
                <tr key={r.run_id}>
                  <td>
                    <code>{r.run_id}</code>
                  </td>
                  <td>{(r.universe ?? []).join(', ')}</td>
                  <td>{r.interval}</td>
                  <td>
                    {r.metrics?.total_return !== undefined
                      ? `${(r.metrics.total_return * 100).toFixed(2)}%`
                      : '—'}
                  </td>
                  <td>{r.metrics?.sharpe !== undefined ? num(r.metrics.sharpe, 3) : '—'}</td>
                  <td>
                    <button onClick={() => void view(r.run_id)}>查看</button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {open && payload ? (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>
            <code>{payload.run_id}</code>
          </h3>
          <table>
            <tbody>
              <tr>
                <th style={{ width: 140 }}>策略</th>
                <td>
                  {payload.report.strategy_key} · {payload.report.engine}
                </td>
              </tr>
              <tr>
                <th>区间</th>
                <td className="muted">
                  {utc(payload.report.start)} → {utc(payload.report.end)}
                </td>
              </tr>
              {Object.entries(payload.report.metrics).map(([k, v]) => (
                <tr key={k}>
                  <th>{k}</th>
                  <td>{num(v, 4)}</td>
                </tr>
              ))}
              <tr>
                <th>制品</th>
                <td>
                  <a href={payload.content_url} target="_blank" rel="noreferrer">
                    {payload.content_url}
                  </a>
                </td>
              </tr>
            </tbody>
          </table>
          <p className="muted" style={{ marginBottom: 0 }}>
            {payload.report.disclaimer}
          </p>
        </div>
      ) : null}
    </div>
  )
}
