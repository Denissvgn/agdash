import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { adoptLinkToken, getJson, localToken } from './client'

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
