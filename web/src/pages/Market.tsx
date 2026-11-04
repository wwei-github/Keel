import { useState } from 'react'
import { api } from '../api/client'
import BarChart, { type Bar } from '../components/BarChart'
import { NotImplementedNotice } from '../components/NotImplemented'
import StaleBanner from '../components/StaleBanner'

/**
 * 行情页。骨架阶段接口返回 501，页面已按最终协议写好，
 * 后端一落地就能出图——先定形状再填实现，返工最少。
 */
export default function Market() {
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [interval, setInterval] = useState('1h')
  const [bars, setBars] = useState<Bar[]>([])
  const [error, setError] = useState<unknown>(null)
  const [stale, setStale] = useState(false)

  async function load() {
    setError(null)
    try {
      const r = await api.get<Bar[]>('/bars', { symbol, interval, start: '2025-01-01T00:00:00Z', end: '2025-03-01T00:00:00Z' })
      setBars(r.data)
      setStale(r.meta.stale)
    } catch (e) {
      setError(e)
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>行情</h2>
      <div className="card" style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
        <input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="BTC/USDT" />
        <select value={interval} onChange={(e) => setInterval(e.target.value)}>
          {['1m', '5m', '15m', '1h', '4h', '1d'].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <button onClick={load}>加载</button>
      </div>
      <StaleBanner stale={stale} asOf={null} />
      {error ? <NotImplementedNotice error={error} /> : null}
      {bars.length > 0 ? (
        <div className="card">
          <BarChart bars={bars} />
        </div>
      ) : null}
    </div>
  )
}
