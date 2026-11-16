import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { newQueryClient } from '../api/queryClient'
import type { EventOut, SessionDetail, ThoughtsCoverage } from '../api/types'

// Synthetic sessions and events only.
const detail = (patch: Partial<SessionDetail> = {}): SessionDetail => ({
  id: 1,
  harness: 'claude_code',
  native_id: 'native-1',
  surface: 'cli',
  title: 'Synthetic long session',
  cwd: '/work/example',
  branch: null,
  state: 'ended',
  started_at: '2026-11-16T09:00:00Z',
  last_activity_at: '2026-11-16T12:00:00Z',
  ended_at: null,
  models: ['model-a'],
  effort: null,
  parent_session_id: null,
  initial_task: 'Synthetic task',
  event_count: 50_000,
  child_count: 0,
  permission_mode: null,
  parent_basis: null,
  children: [],
  events_by_kind: {},
  ...patch,
})

const event = (sequence: number, patch: Partial<EventOut> = {}): EventOut => ({
  id: sequence + 1,
  session_id: 1,
  sequence,
  occurred_at: '2026-11-16T09:00:00Z',
  kind: 'assistant_text',
  payload: { text: `Synthetic reply ${sequence}` },
  thoughts_coverage: null,
  ...patch,
})

function serve(session: SessionDetail, events: (cursor: number, limit: number) => EventOut[], total: number) {
  const pages: number[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
      if (url.pathname === `/api/sessions/${session.id}`) return json(session)
      if (url.pathname === `/api/sessions/${session.id}/events`) {
        const after = url.searchParams.get('cursor') ? Number(url.searchParams.get('cursor')) : -1
        const limit = Number(url.searchParams.get('limit'))
        pages.push(after)
        const items = events(after + 1, limit)
        const last = items.at(-1)?.sequence ?? after
        return json({ items, next_cursor: last + 1 < total ? String(last) : null })
      }
      if (url.pathname === '/api/sessions') return json({ items: [], next_cursor: null })
      return json({ detail: 'not found' }, 404)
    }),
  )
  return pages
}

function open(path: string) {
  window.location.hash = `#${path}`
  render(<App queryClient={newQueryClient()} />)
}

beforeEach(() => {
  window.__AGDASH__ = { token: 'test-token' }
})

afterEach(() => vi.unstubAllGlobals())

describe('a session of 50,000 events', () => {
  const TOTAL = 50_000
  const pageOf = (from: number, limit: number) =>
    Array.from({ length: Math.max(0, Math.min(limit, TOTAL - from)) }, (_, i) => event(from + i))

  test('draws only the rows near the view and loads more as it scrolls', async () => {
    const pages = serve(detail(), pageOf, TOTAL)
    open('/history/sessions/1')
    const region = await screen.findByRole('region', { name: 'Session timeline' })
    const list = within(region).getByRole('list')
    expect(screen.getByText('500 of 50000 events loaded')).toBeInTheDocument()
    // Every row says where it sits among all 50,000, but only a window of them exists.
    const drawn = within(list).getAllByRole('listitem')
    expect(drawn.length).toBeGreaterThan(5)
    expect(drawn.length).toBeLessThan(60)
    expect(drawn[0]).toHaveAttribute('aria-setsize', '50000')
    expect(pages).toEqual([-1])

    // Scroll to the end of what is loaded: the next page is fetched, and still only a
    // window of rows is drawn.
    const height = Number.parseFloat(list.style.height)
    region.scrollTop = height - 400
    fireEvent.scroll(region)
    await waitFor(() => expect(pages).toEqual([-1, 499]))
    await screen.findByText('1000 of 50000 events loaded')
    expect(within(list).getAllByRole('listitem').length).toBeLessThan(60)
    expect(within(list).getByText('Synthetic reply 499')).toBeInTheDocument()

    // In the middle, nothing more is fetched.
    region.scrollTop = 20_000
    fireEvent.scroll(region)
    expect(pages).toEqual([-1, 499])
    expect(within(list).queryByText('Synthetic reply 0')).not.toBeInTheDocument()
  })
})

describe('what each event shows', () => {
  const coverages: (ThoughtsCoverage | null)[] = ['full', 'summary', 'encrypted', 'absent', null]
  const events = [
    event(0, { kind: 'user_prompt', payload: { text: 'Synthetic prompt' } }),
    ...coverages.map((coverage, i) =>
      event(1 + i, {
        kind: 'thinking',
        thoughts_coverage: coverage,
        payload: coverage === 'full' || coverage === 'summary' ? { text: `Synthetic thought ${coverage}` } : {},
      }),
    ),
    event(6, { kind: 'tool_call', payload: { name: 'shell', call_id: 'c1', input: { command: 'ls' } } }),
    event(7, { kind: 'tool_result', payload: { name: 'shell', call_id: 'c1', is_error: true, output: 'x'.repeat(3000) } }),
    event(8, { kind: 'subagent', payload: { phase: 'started', agent_type: 'explorer' } }),
  ]

  test('thoughts show their coverage, and calls and results their tool', async () => {
    serve(detail({ event_count: events.length, children: [7] }), (from, limit) => events.slice(from, from + limit), events.length)
    open('/history/sessions/1')
    const region = await screen.findByRole('region', { name: 'Session timeline' })
    for (const label of [
      'Full reasoning',
      'Summary of reasoning',
      'Encrypted, not readable',
      'Reasoning not kept',
      'Coverage not recorded',
    ]) {
      expect(within(region).getByText(label)).toBeInTheDocument()
    }
    expect(within(region).getByText('Synthetic thought full')).toBeInTheDocument()
    expect(within(region).getByText('Tool call · shell')).toBeInTheDocument()
    expect(within(region).getByText('Tool result · shell')).toBeInTheDocument()
    expect(within(region).getByText('Error')).toBeInTheDocument()
    expect(within(region).getByText('Subagent started')).toBeInTheDocument()
    expect(screen.getByText('9 events')).toBeInTheDocument()

    const more = within(region).getByRole('button', { name: 'Show all 3000 characters' })
    fireEvent.click(more)
    expect(within(region).getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: 'Subagent 1' })).toHaveAttribute('href', '#/history/sessions/7')
  })

  test('an address that names no session says so', async () => {
    serve(detail(), () => [], 0)
    open('/history/sessions/not-a-number')
    expect(await screen.findByRole('heading', { level: 1, name: 'Session not found' })).toBeInTheDocument()
  })
})
