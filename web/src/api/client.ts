/**
 * 后端 HTTP 客户端。
 *
 * 只做三件事：拼 URL、解 Envelope、把错误规格化成 `ApiError`。
 * 页面组件永远看到的是 `data`，不是 `{ data, meta }` —— 让每个页面自己拆包，
 * 迟早有一处忘了读 `meta.stale`，然后那条带了过期数据的曲线就被人当真了。
 */

const BASE = '/api/v1'

export interface Meta {
  request_id: string | null
  as_of: string | null
  /** 数据修订导致快照失效。页面必须显示提示，不得静默使用。 */
  stale: boolean
}

export interface Envelope<T> {
  data: T
  meta: Meta
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function token(): string | null {
  return localStorage.getItem('keel.api_token')
}

async function parse<T>(resp: Response): Promise<{ data: T; meta: Meta }> {
  const text = await resp.text()
  const body = text ? JSON.parse(text) : {}

  if (!resp.ok) {
    const err = body?.error ?? {}
    throw new ApiError(
      err.code ?? 'INTERNAL',
      err.message ?? resp.statusText,
      resp.status,
      err.details ?? {},
    )
  }
  // 成功响应必须带 meta；缺了就当 stale，宁可多提示也不要静默
  return { data: body.data as T, meta: body.meta ?? { request_id: null, as_of: null, stale: false } }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<{ data: T; meta: Meta }> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  const t = token()
  if (t) headers.set('Authorization', `Bearer ${t}`)

  const resp = await fetch(`${BASE}${path}`, { ...init, headers })
  return parse<T>(resp)
}

export const api = {
  get: <T>(path: string, params?: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params ?? {})) {
      if (v !== undefined && v !== '') qs.set(k, String(v))
    }
    const suffix = qs.toString() ? `?${qs}` : ''
    return request<T>(`${path}${suffix}`)
  },
  post: <T>(path: string, body: unknown, idempotencyKey?: string) => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey
    return request<T>(path, { method: 'POST', body: JSON.stringify(body), headers })
  },
}

export interface HealthData {
  status: string
  version: string
  components: Record<string, string>
}
