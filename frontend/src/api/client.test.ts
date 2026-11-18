import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { adoptLinkToken, ApiError, getBlob, getJson, localToken, REQUEST_TIMEOUT_MS, sendJson } from './client'

beforeEach(() => {
  window.sessionStorage.clear()
  window.__AGDASH__ = undefined
  window.history.replaceState(null, '', '/')
})

afterEach(() => vi.unstubAllGlobals())

test('a link from agdash open hands the token to this tab and leaves the address', async () => {
  window.history.replaceState(null, '', '/#agdash-token=link-token-123')
  adoptLinkToken()
  expect(window.location.hash).toBe('#/live')
  expect(window.location.href).not.toContain('link-token-123')
  expect(localToken()).toBe('link-token-123')

  const fetched = vi.fn(async () => new Response('{}'))
  vi.stubGlobal('fetch', fetched)
  await getJson('/api/stats')
  const [, init] = fetched.mock.calls[0] as unknown as [string, RequestInit]
  expect((init.headers as Record<string, string>).Authorization).toBe('Bearer link-token-123')
})

test('an ordinary address is left alone', () => {
  window.history.replaceState(null, '', '/#/history')
  adoptLinkToken()
  expect(window.location.hash).toBe('#/history')
  expect(localToken()).toBeUndefined()
})

test('the desktop app’s token wins over one a link left', () => {
  window.sessionStorage.setItem('agdash.token', 'from-a-link')
  window.__AGDASH__ = { token: 'from-the-app' }
  expect(localToken()).toBe('from-the-app')
})

/** A fetch whose answer never comes, as when a network path drops the request: it only ends if aborted. */
function silentFetch() {
  return vi.fn(
    (_: string, init?: RequestInit) =>
      new Promise<Response>((_, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      }),
  )
}

test('a read that never gets an answer fails as unreachable after the timeout, so it can be retried', async () => {
  vi.useFakeTimers()
  try {
    vi.stubGlobal('fetch', silentFetch())
    const read = getJson('/api/sessions/1/events')
    const outcome = read.then(
      () => 'answered',
      (error: unknown) => error,
    )
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1)
    expect(await Promise.race([outcome, Promise.resolve('still waiting')])).toBe('still waiting')
    await vi.advanceTimersByTimeAsync(1)
    const error = await outcome
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).kind).toBe('unreachable')
  } finally {
    vi.useRealTimers()
  }
})

test.each([502, 503, 504])('a gateway that cannot reach the service (%i) reads as unreachable, not as its failure', async (status) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status })))
  const error = await getJson('/api/sessions').catch((caught: unknown) => caught)
  expect(error).toBeInstanceOf(ApiError)
  expect((error as ApiError).kind).toBe('unreachable')
})

test('the service saying it is preparing its store reads as that, not as unreachable', async () => {
  const answer = () =>
    new Response(JSON.stringify({ detail: 'the service is preparing its store', status: 'preparing' }), { status: 503 })
  vi.stubGlobal('fetch', vi.fn(async () => answer()))
  for (const ask of [() => getJson('/api/sessions'), () => getBlob('/api/sessions/1/export'), () => sendJson('POST', '/api/storage/settings', {})]) {
    const error = await ask().catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).kind).toBe('preparing')
    expect((error as ApiError).status).toBe(503)
  }
})

test('a write that is refused still carries the service’s own words', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'no limit is set' }), { status: 409 })))
  const error = await sendJson('POST', '/api/storage/retention/removal').catch((caught: unknown) => caught)
  expect((error as ApiError).kind).toBe('server')
  expect((error as ApiError).detail).toBe('no limit is set')
})

test("the caller's cancel is still a cancel, not an error, and clears the timeout", async () => {
  vi.useFakeTimers()
  try {
    vi.stubGlobal('fetch', silentFetch())
    const controller = new AbortController()
    const read = getJson('/api/sessions/1/events', {}, controller.signal).catch((error: unknown) => error)
    controller.abort()
    const error = await read
    expect(error).not.toBeInstanceOf(ApiError)
    expect((error as DOMException).name).toBe('AbortError')
    expect(vi.getTimerCount()).toBe(0)
  } finally {
    vi.useRealTimers()
  }
})
