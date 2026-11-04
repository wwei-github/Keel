import { ApiError } from '../api/client'

/**
 * 把 501 渲染成"这个功能还没做"，而不是"出错了"。
 *
 * 骨架阶段前后端都要能跑通，接口没实现是预期状态；
 * 把它当成错误弹红框，会让人误以为系统坏了。
 */
export function NotImplementedNotice({ error }: { error: unknown }) {
  const isNotImpl = error instanceof ApiError && error.code === 'NOT_IMPLEMENTED'
  return (
    <div className="card">
      <span className={isNotImpl ? 'badge' : 'badge error'}>
        {isNotImpl ? '未实现' : '请求失败'}
      </span>
      <p className="muted" style={{ marginBottom: 0 }}>
        {error instanceof Error ? error.message : String(error)}
      </p>
    </div>
  )
}
