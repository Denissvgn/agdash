/**
 * The agdash service's API. Every request carries the local token when the
 * desktop app provides one; in development the Vite proxy adds it instead. It
 * reads, except for settings kept in agdash itself (see ``sendJson``).
 */

declare global {
  interface Window {
    /** Set by the desktop shell before the page loads. */
    __AGDASH__?: { apiBase?: string; token?: string }
  }
}

const LINK_PREFIX = '#agdash-token='
const STORED = 'agdash.token'

/**
 * Take the token from a link `agdash open` printed (`/#agdash-token=…`): keep it for
 * this tab only and take it out of the address, so it is not bookmarked or shared.
 * A browser never sends the part after `#` to a server.
 */
export function adoptLinkToken(): void {
  if (!window.location.hash.startsWith(LINK_PREFIX)) return
  const token = decodeURIComponent(window.location.hash.slice(LINK_PREFIX.length))
  try {
    if (token) window.sessionStorage.setItem(STORED, token)
  } catch {
    // Storage refused: the page then asks the service without a token and says so.
  }
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#/live`)
}

/** The token the desktop app handed over, or the one a link gave this tab. */
export function localToken(): string | undefined {
  const handed = window.__AGDASH__?.token
  if (handed) return handed
  try {
    return window.sessionStorage.getItem(STORED) ?? undefined
  } catch {
    return undefined
  }
}

/**
 * ``preparing`` is the service itself saying it is still bringing its store up to date:
 * it runs, and will answer once that is done, so a page says so and asks again.
 */
export type ApiErrorKind = 'unreachable' | 'preparing' | 'refused' | 'missing' | 'invalid' | 'server'

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
  // A gateway in between, such as the dev server's proxy, could not reach the service.
  if (status === 502 || status === 503 || status === 504) return 'unreachable'
  return 'server'
}

/**
 * How long a read may take before it counts as lost. A request a network path drops
 * without an answer would otherwise never settle, and its page would wait for good;
 * timed out, it fails as unreachable and the query retries or shows its notice.
 */
export const REQUEST_TIMEOUT_MS = 30_000

/**
 * What a failed answer means. A 503 whose body says ``preparing`` comes from the service,
 * not from something in between that could not reach it.
 */
async function failureOf(response: Response, withDetail = false): Promise<ApiError> {
  if (response.status !== 503 && !withDetail) return new ApiError(kindFor(response.status), response.status)
  let body: { detail?: unknown; status?: unknown } = {}
  try {
    body = ((await response.json()) ?? {}) as typeof body
  } catch {
    // No body, or not JSON: only the status speaks.
  }
  const detail = typeof body.detail === 'string' ? body.detail : null
  if (response.status === 503 && body.status === 'preparing') return new ApiError('preparing', 503, detail)
  return new ApiError(kindFor(response.status), response.status, withDetail ? detail : null)
}

export async function getJson<T>(path: string, params: Params = {}, signal?: AbortSignal): Promise<T> {
  const config = window.__AGDASH__ ?? {}
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = localToken()
  if (token) headers.Authorization = `Bearer ${token}`
  // One signal for the caller's cancel and the timeout (AbortSignal.any is too new for
  // every web view agdash runs in).
  const controller = new AbortController()
  const cancel = () => controller.abort(signal?.reason)
  if (signal?.aborted) cancel()
  signal?.addEventListener('abort', cancel, { once: true })
  const timer = setTimeout(() => controller.abort(new DOMException('timed out', 'TimeoutError')), REQUEST_TIMEOUT_MS)
  try {
    let response: Response
    try {
      response = await fetch(`${config.apiBase ?? ''}${path}${query(params)}`, { headers, signal: controller.signal })
    } catch (error) {
      if (signal?.aborted) throw error
      throw new ApiError('unreachable', null)
    }
    if (!response.ok) throw await failureOf(response)
    return (await response.json()) as T
  } catch (error) {
    // A body cut off by the timeout is a lost request too, unless the caller cancelled.
    if (controller.signal.aborted && !signal?.aborted && !(error instanceof ApiError)) throw new ApiError('unreachable', null)
    throw error
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', cancel)
  }
}

/**
 * A file the service makes on request, such as a session's export. It carries the token
 * as every read does, which a plain link could not, and has no time limit: a long
 * session takes as long as it takes, and the caller can cancel.
 */
export async function getBlob(path: string, params: Params = {}, signal?: AbortSignal): Promise<Blob> {
  const config = window.__AGDASH__ ?? {}
  const headers: Record<string, string> = {}
  const token = localToken()
  if (token) headers.Authorization = `Bearer ${token}`
  let response: Response
  try {
    response = await fetch(`${config.apiBase ?? ''}${path}${query(params)}`, { headers, signal })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new ApiError('unreachable', null)
  }
  if (!response.ok) throw await failureOf(response)
  try {
    return await response.blob()
  } catch (error) {
    if (signal?.aborted) throw error
    throw new ApiError('unreachable', null)
  }
}

/**
 * The only requests the app sends that change anything, and only in agdash itself: the
 * WorkChord connector's settings (pairing, rules and pins), and agdash's own on the Data
 * page (how long history is kept, whether original lines are, and removing what is
 * past the limit). Answers 204 as null.
 */
export async function sendJson<T>(method: 'POST' | 'DELETE', path: string, body?: unknown): Promise<T | null> {
  const config = window.__AGDASH__ ?? {}
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = localToken()
  if (token) headers.Authorization = `Bearer ${token}`
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
  if (!response.ok) throw await failureOf(response, true)
  if (response.status === 204) return null
  return (await response.json()) as T
}
