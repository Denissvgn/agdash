import { QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { Figure } from '../charts/Figure'
import { i18n } from '../i18n'
import { getJson } from './client'
import { usePreparing } from './preparing'
import { newQueryClient } from './queryClient'

const PREPARING = { detail: 'the service is preparing its store', status: 'preparing' }

beforeEach(() => {
  window.__AGDASH__ = undefined
})

afterEach(() => vi.unstubAllGlobals())

/** A page: it asks for something, and draws a figure whose values did not come. */
function Page() {
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: ({ signal }) => getJson('/api/sessions', {}, signal) })
  return (
    <>
      <p>{sessions.isSuccess ? 'loaded' : sessions.isError ? 'failed' : 'loading'}</p>
      <p>{usePreparing() ? 'preparing' : 'not preparing'}</p>
      <Figure height={120} status={sessions.isSuccess ? 'empty' : 'error'} title="Synthetic figure" onRetry={() => undefined} />
    </>
  )
}

const ASK_MS = 40
const pause = (ms: number) => act(async () => void (await new Promise((done) => setTimeout(done, ms))))

test('a preparing service is watched until it is ready, and then everything is asked for again', async () => {
  let preparing = true
  const asked: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      asked.push(input)
      if (input === '/health') return new Response(JSON.stringify({ status: preparing ? 'preparing' : 'ok' }))
      return preparing ? new Response(JSON.stringify(PREPARING), { status: 503 }) : new Response(JSON.stringify({ items: [] }))
    }),
  )
  const client = newQueryClient(ASK_MS)
  const invalidated = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <Page />
      </I18nextProvider>
    </QueryClientProvider>,
  )
  expect(await screen.findByText('preparing')).toBeInTheDocument()
  expect(screen.getByText('failed')).toBeInTheDocument()
  expect(screen.getByText('The service is preparing its store')).toBeInTheDocument()
  expect(screen.getByText('This figure loads by itself once the service is ready.')).toBeInTheDocument()
  expect(screen.queryByText('This figure could not load')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()

  // While it prepares, only its health is asked for: the page is asked for once, not
  // again and again, since a retry cannot help.
  await pause(ASK_MS * 4)
  expect(asked.filter((path) => path === '/api/sessions')).toHaveLength(1)
  expect(asked.filter((path) => path === '/health').length).toBeGreaterThanOrEqual(2)
  expect(invalidated).not.toHaveBeenCalled()
  expect(screen.getByText('preparing')).toBeInTheDocument()

  preparing = false
  expect(await screen.findByText('loaded')).toBeInTheDocument()
  expect(screen.getByText('not preparing')).toBeInTheDocument()
  expect(screen.queryByText('The service is preparing its store')).not.toBeInTheDocument()
  expect(invalidated).toHaveBeenCalledTimes(1)
  expect(asked.filter((path) => path === '/api/sessions')).toHaveLength(2)

  // Ready is ready: the watch is over and asks nothing more.
  const before = asked.length
  await pause(ASK_MS * 4)
  expect(asked).toHaveLength(before)
})

test('a figure drawn with no app around it is never preparing', () => {
  render(
    <I18nextProvider i18n={i18n}>
      <Figure height={120} status="error" title="Synthetic figure" onRetry={() => undefined} />
    </I18nextProvider>,
  )
  expect(screen.getByText('This figure could not load')).toBeInTheDocument()
})
