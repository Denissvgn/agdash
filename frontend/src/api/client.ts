/**
 * The agdash service's API. Every request carries the local token when the
 * desktop app provides one; in development the Vite proxy adds it instead. It
 * reads, except for the WorkChord connector's own settings (see ``sendJson``).
 */

declare global {
  interface Window {
    /** Set by the desktop shell before the page loads. */
    __AGDASH__?: { apiBase?: string; token?: string }
  }
}

export type ApiErrorKind = 'unreachable' | 'refused' | 'missing' | 'invalid' | 'server'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  /** The service's own explanation, when it gave one in words. */
  readonly detail: string | null

  constructor(kind: ApiErrorKind, status: number | null, detail: string | null = null) {
    super(kind)
    this.kind = kind
    this.status = status
    this.detail = detail
  }
}

export type Params = Record<string, string | number | boolean | string[] | null | undefined>

export function query(params: Params): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '' || value === false) continue
    if (Array.isArray(value)) value.forEach((item) => search.append(key, item))
    else search.append(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

function kindFor(status: number): ApiErrorKind {
  if (status === 401 || status === 403 || status === 421) return 'refused'
  if (status === 404) return 'missing'
  if (status === 400 || status === 422) return 'invalid'
  return 'server'
}

export async function getJson<T>(path: string, params: Params = {}, signal?: AbortSignal): Promise<T> {
  const config = window.__AGDASH__ ?? {}
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (config.token) headers.Authorization = `Bearer ${config.token}`
  let response: Response
  try {
    response = await fetch(`${config.apiBase ?? ''}${path}${query(params)}`, { headers, signal })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new ApiError('unreachable', null)
  }
  if (!response.ok) throw new ApiError(kindFor(response.status), response.status)
  return (await response.json()) as T
}

async function detailOf(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    return typeof body.detail === 'string' ? body.detail : null
  } catch {
    return null
  }
}

/**
 * The only requests the app sends that change anything: the WorkChord connector's
 * settings in agdash (pairing, rules and pins). Answers 204 as null.
 */
export async function sendJson<T>(method: 'POST' | 'DELETE', path: string, body?: unknown): Promise<T | null> {
  const config = window.__AGDASH__ ?? {}
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (config.token) headers.Authorization = `Bearer ${config.token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch(`${config.apiBase ?? ''}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('unreachable', null)
  }
  if (!response.ok) throw new ApiError(kindFor(response.status), response.status, await detailOf(response))
  if (response.status === 204) return null
  return (await response.json()) as T
}
