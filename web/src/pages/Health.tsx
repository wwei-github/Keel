import { useEffect, useState } from 'react'
import { api, type HealthData } from '../api/client'
import { NotImplementedNotice } from '../components/NotImplemented'

/**
 * 系统状态页。骨架阶段这是唯一真正通数据的页面——
 * 有一个端到端跑通的接口，才能确认代理、鉴权、Envelope 解包都是对的。
 */
export default function Health() {
  const [data, setData] = useState<HealthData | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    api
      .get<HealthData>('/health')
      .then((r) => alive && setData(r.data))
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  if (loading) return <p className="muted">加载中…</p>
  if (error) return <NotImplementedNotice error={error} />
  if (!data) return null

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>系统状态</h2>
      <div className="card">
        <table>
          <tbody>
            <tr>
              <th style={{ width: 140 }}>服务</th>
              <td>{data.status}</td>
            </tr>
            <tr>
              <th>版本</th>
              <td>{data.version}</td>
            </tr>
            <tr>
              <th>组件</th>
              <td className="muted">
                {Object.keys(data.components).length === 0
                  ? '骨架阶段未探活'
                  : Object.entries(data.components)
                      .map(([k, v]) => `${k}=${v}`)
                      .join('  ')}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ marginTop: 12 }}>
        健康检查只报进程与版本，不探数据库：探库的检查会在 PG 重启时把 API 一起判死。
      </p>
    </div>
  )
}
