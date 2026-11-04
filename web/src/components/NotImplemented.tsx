import { ApiError } from '../api/client'

/**
 * 把 501 渲染成"这个功能还没做"，而不是"出错了"。
 *
 * 骨架阶段前后端都要能跑通，接口没实现是预期状态；
 * 把它当成错误弹红框，会让人误以为系统坏了。
 */
export function ErrorNotice({ error }: { error: unknown }) {
  const code = error instanceof ApiError ? error.code : null
  const label =
    code === 'NOT_IMPLEMENTED'
      ? '未实现'
      : code === 'NOT_FOUND'
        ? '没找到'
        : code === 'DATA_NOT_COVERED'
          ? '数据未覆盖'
          : code === 'BLOCKING_GAP'
            ? '存在阻断缺口'
            : '请求失败'
  const kind =
    code === 'NOT_IMPLEMENTED' ? 'badge' : code === 'NOT_FOUND' ? 'badge' : 'badge error'
  return (
    <div className="card">
      <span className={kind}>{label}</span>
      <p className="muted" style={{ marginBottom: 0 }}>
        {error instanceof Error ? error.message : String(error)}
      </p>
    </div>
  )
}

/** 兼容旧名：骨架阶段到处用它表示 501。 */
export const NotImplementedNotice = ErrorNotice
