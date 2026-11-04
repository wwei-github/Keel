import { useCallback, useEffect, useState } from 'react'
import { api, type Meta } from '../api/client'
import BarChart, { type Bar } from '../components/BarChart'
import { ErrorNotice } from '../components/NotImplemented'
import StaleBanner from '../components/StaleBanner'
import { num, utc } from '../api/format'

interface Coverage {
  symbol: string
  interval: string
  start?: string | null
  end?: string | null
  expected?: number
  present?: number
  blocking_gaps?: number
  gaps?: { start: string; end: string; missing: number; severity: string }[]
}

/**
 * 行情页 = K 线 + 覆盖率。
 *
 * 两个请求打同一个页面是有意的：只看图不看覆盖率，看到的是"这条曲线很好看"，
 * 而它可能是三段数据拼出来的。缺口必须和曲线同时出现在视野里。
 */
export default function Market() {
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [interval, setInterval] = useState('1h')
  const [from, setFrom] = useState('2024-01-01T00:00:00Z')
  const [to, setTo] = useState('2024-02-01T00:00:00Z')
  const [bars, setBars] = useState<Bar[]>([])
  const [coverage, setCoverage] = useState<Coverage | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // API-01 的参数是 from/to（后端用 Query(alias=...)），这里必须照协议写
      const [barsResp, covResp] = await Promise.all([
        api.get<Bar[]>('/bars', { symbol, interval, from, to }),
        api.get<Coverage>('/coverage', { symbol, interval }),
      ])
      setBars(barsResp.data)
      setMeta(barsResp.meta)
      setCoverage(covResp.data)
    } catch (e) {
      setError(e)
      setBars([])
      setCoverage(null)
    } finally {
      setLoading(false)
    }
  }, [symbol, interval, from, to])

  useEffect(() => {
    void load()
  }, [load])

  const ratio =
    coverage?.expected && coverage.present !== undefined
      ? coverage.present / coverage.expected
      : null

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>行情</h2>
      <div className="card" style={{ marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="BTC/USDT" />
        <select value={interval} onChange={(e) => setInterval(e.target.value)}>
          {['1m', '5m', '15m', '1h', '4h', '1d'].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <input value={from} onChange={(e) => setFrom(e.target.value)} aria-label="from" />
        <input value={to} onChange={(e) => setTo(e.target.value)} aria-label="to" />
        <button onClick={() => void load()} disabled={loading}>
          {loading ? '加载中…' : '加载'}
        </button>
      </div>

      <StaleBanner stale={meta?.stale ?? false} asOf={meta?.as_of ?? null} />

      {error ? <ErrorNotice error={error} /> : null}

      {coverage ? (
        <div className="card" style={{ marginBottom: 16 }}>
          <table>
            <tbody>
              <tr>
                <th style={{ width: 140 }}>覆盖区间</th>
                <td>
                  {utc(coverage.start)} → {utc(coverage.end)}
                </td>
              </tr>
              <tr>
                <th>应有 / 实际</th>
                <td>
                  {num(coverage.expected, 0)} / {num(coverage.present, 0)}
                  {ratio !== null ? (
                    <span className={ratio >= 0.995 ? 'muted' : 'badge warn'} style={{ marginLeft: 8 }}>
                      {(ratio * 100).toFixed(2)}%
                    </span>
                  ) : null}
                </td>
              </tr>
              <tr>
                <th>阻断缺口</th>
                <td>
                  {coverage.blocking_gaps ?? 0}
                  {coverage.blocking_gaps ? (
                    <span className="badge error" style={{ marginLeft: 8 }}>
                      回测结果不可信
                    </span>
                  ) : null}
                </td>
              </tr>
              {(coverage.gaps ?? []).length > 0 ? (
                <tr>
                  <th>缺口明细</th>
                  <td className="muted">
                    {(coverage.gaps ?? [])
                      .map((g) => `${utc(g.start)} 缺 ${g.missing} 根（${g.severity}）`)
                      .join('；')}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="card">
        {bars.length > 0 ? (
          <BarChart bars={bars} />
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            {loading ? '加载中…' : '该区间没有数据。先跑回填：make backfill'}
          </p>
        )}
      </div>
    </div>
  )
}
