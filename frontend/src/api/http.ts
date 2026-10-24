/** The backend's HTTP verbs, shared by every `api/` group. */
export const BASE = '/api'

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export async function put<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

/**
 * A POST whose FastAPI `detail` survives into the thrown Error.
 *
 * `post()` above throws "409 Conflict", which is exactly the generic failure
 * the storage screen must not show: Windows refuses an eject while a game is
 * still reading the disk, and that sentence is the only actionable part of
 * the response. Losing it turns a fixable state into a dead end.
 */
export async function postDetailed<T>(path: string, body?: unknown): Promise<T> {
  return sendDetailed<T>('POST', path, body)
}

/** Any verb, with the same `detail`-keeping error as `postDetailed`. */
export async function sendDetailed<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const payload = await r.json().catch(() => null)
  if (!r.ok) throw new Error(payload?.detail || `${r.status} ${r.statusText}`)
  return payload as T
}

export async function del<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
