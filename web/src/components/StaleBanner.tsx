/**
 * 数据陈旧提示（§6.3）。
 *
 * 这个组件的存在是个约定：凡是渲染 `meta.stale` 数据的页面，都必须挂它。
 * "数据过期但还能看"是最危险的中间态——不提示就会有人拿它做决定。
 */
export default function StaleBanner({ stale, asOf }: { stale: boolean; asOf: string | null }) {
  if (!stale) return null
  return (
    <div className="card" style={{ borderColor: 'var(--warn)', marginBottom: 16 }}>
      <span className="badge warn">数据已过期</span>
      <span className="muted" style={{ marginLeft: 8 }}>
        上游数据发生修订，当前结果可能失效{asOf ? `（截止 ${asOf}）` : ''}。请重新运行后再使用。
      </span>
    </div>
  )
}
