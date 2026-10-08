/** Our own backend only; secrets never enter browser state. */
export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message) }
}
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) } })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = body.detail
    const message = typeof detail === 'string' ? detail : JSON.stringify(detail || `HTTP ${response.status}`)
    throw new ApiError(response.status === 409 && path.startsWith('/questions/') ? `${message}；请重新加载草稿，不要覆盖其他版本。` : message, response.status)
  }
  return response.json() as Promise<T>
}
