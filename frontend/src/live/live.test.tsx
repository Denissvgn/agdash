import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { newQueryClient } from '../api/queryClient'
import type { Page, SessionSummary } from '../api/types'
import { ENDED_WINDOW_MS, UNKNOWN_WINDOW_MS, forest, groups, split, stateCounts } from './board'
import { formatAgo } from './format'
import { SILENCE_MS, parseEvents } from './stream'

// Synthetic sessions: no real transcript content appears in tests.
const NOW = Date.parse('2026-11-17T12:00:00Z')
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString()

const session = (id: number, patch: Partial<SessionSummary> = {}): SessionSummary => ({
  id,
  harness: 'claude_code',
  native_id: `native-${id}`,
  surface: 'cli',
  title: `Synthetic session ${id}`,
  cwd: '/work/orchard',
  branch: null,
  state: 'active',
  state_basis: 'harness_busy',
  started_at: ago(30),
  last_activity_at: ago(1),
  ended_at: null,
  models: ['model-a'],
  effort: 'high',
  parent_session_id: null,
  initial_task: 'Synthetic task',
  event_count: 12,
  child_count: 0,
  ...patch,
})

describe('arranging the board', () => {
  test('live, unknown and just-ended sessions are told apart by their windows', () => {
    const { live, ended } = split(
      [
        session(1),
        session(2, { state: 'unknown', last_activity_at: ago(10) }),
        session(3, { state: 'unknown', last_activity_at: new Date(NOW - UNKNOWN_WINDOW_MS - 60_000).toISOString() }),
        session(4, { state: 'ended', ended_at: ago(5) }),
        session(5, { state: 'ended', ended_at: new Date(NOW - ENDED_WINDOW_MS - 60_000).toISOString() }),
        session(1),
      ],
      NOW,
    )
    expect(live.map((s) => s.id).sort()).toEqual([1, 2])
    expect(ended.map((s) => s.id)).toEqual([4])
  })

  test('children sit under their parent; a child whose parent is not live stands alone', () => {
    const roots = forest([
      session(1),
      session(2, { parent_session_id: 1, harness: 'codex' }),
      session(3, { parent_session_id: 2 }),
      session(4, { parent_session_id: 99 }),
    ])
    expect(roots.map((n) => n.session.id).sort()).toEqual([1, 4])
    const parent = roots.find((n) => n.session.id === 1)!
    expect(parent.children.map((n) => n.session.id)).toEqual([2])
    expect(parent.children[0].children.map((n) => n.session.id)).toEqual([3])
    expect(roots.find((n) => n.session.id === 4)!.orphan).toBe(true)
  })

  test('a parent loop does not hide its sessions', () => {
    const roots = forest([session(1, { parent_session_id: 2 }), session(2, { parent_session_id: 1 })])
    const all = (nodes: ReturnType<typeof forest>): number[] =>
      nodes.flatMap((n) => [n.session.id, ...all(n.children)])
    expect(all(roots).sort()).toEqual([1, 2])
  })

  test('groups follow the root, most recently active first, children counted', () => {
    const sessions = [
      session(1, { cwd: '/work/orchard', last_activity_at: ago(20) }),
      session(2, { cwd: '/elsewhere', parent_session_id: 1 }),
      session(3, { cwd: '/work/meadow', harness: 'pi', last_activity_at: ago(2) }),
      session(4, { cwd: null, harness: 'codex', last_activity_at: ago(40) }),
    ]
    expect(groups(sessions, 'folder').map((g) => [g.key, g.count])).toEqual([
      ['/work/orchard', 2],
      ['/work/meadow', 1],
      [null, 1],
    ])
    expect(groups(sessions, 'harness').map((g) => g.key)).toEqual(['claude_code', 'pi', 'codex'])
  })

  test('by harness, a group counts its own sessions and names the others nested in it', () => {
    const [group, ...rest] = groups(
      [session(1), session(2, { harness: 'codex', parent_session_id: 1 }), session(3, { harness: 'codex' })],
      'harness',
    )
    expect([group.key, group.count, group.others]).toEqual(['claude_code', 1, 1])
    expect(rest.map((g) => [g.key, g.count, g.others])).toEqual([['codex', 1, 0]])
  })

  test('state counts leave out states nobody is in', () => {
    expect(stateCounts([session(1), session(2, { state: 'waiting' }), session(3)])).toEqual([
      ['active', 2],
      ['waiting', 1],
    ])
  })

  test('times read as words', () => {
    expect(formatAgo(NOW - 20_000, NOW)).toBe('now')
    expect(formatAgo(NOW - 5 * 60_000, NOW)).toBe('5 minutes ago')
    expect(formatAgo(NOW - 3 * 3_600_000, NOW)).toBe('3 hours ago')
  })
})

describe('reading the stream', () => {
  test('messages are parsed, comments skipped and a partial message kept', () => {
    const [messages, rest] = parseEvents(
      'event: hello\ndata: {"from":{}}\n\n: still here\n\nevent: sessions\ndata: {"sessions":[{"id":3}]}\n\nevent: ev',
    )
    expect(messages).toEqual([
      { event: 'hello', data: { from: {} } },
      { event: 'sessions', data: { sessions: [{ id: 3 }] } },
    ])
    expect(rest).toBe('event: ev')
  })
})

// -- the page, against a stand-in service ----------------------------------------------

/** The open stream connection; like real fetch, aborting the request ends it. */
class Stream {
  private controller?: ReadableStreamDefaultController<Uint8Array>
  private encoder = new TextEncoder()
  body = this.open()

  open(signal?: AbortSignal | null): ReadableStream<Uint8Array> {
    this.body = new ReadableStream<Uint8Array>({ start: (c) => void (this.controller = c) })
    const current = this.controller
    signal?.addEventListener('abort', () => current?.error(new DOMException('aborted', 'AbortError')))
    return this.body
  }

  send(event: string, data: unknown) {
    this.controller?.enqueue(this.encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`))
  }
}

function serve(state: { sessions: SessionSummary[]; prompts: Record<number, string> }) {
  const stream = new Stream()
  const calls: URL[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      calls.push(url)
      if (url.pathname === '/api/stream') return new Response(stream.open(init?.signal), { status: 200 })
      const events = url.pathname.match(/^\/api\/sessions\/(\d+)\/events$/)
      if (events) {
        const text = state.prompts[Number(events[1])]
        const items = text ? [{ kind: 'user_prompt', payload: { text } }] : []
        return new Response(JSON.stringify({ items, next_cursor: null }))
      }
      if (url.pathname === '/api/sessions') {
        const wanted = url.searchParams.getAll('state')
        const body: Page<SessionSummary> = {
          items: state.sessions.filter((s) => wanted.includes(s.state)),
          next_cursor: null,
        }
        return new Response(JSON.stringify(body))
      }
      return new Response('{}', { status: 404 })
    }),
  )
  return { stream, calls }
}

function open(hash = '#/live') {
  window.location.hash = hash
  render(<App queryClient={newQueryClient()} />)
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  window.location.hash = ''
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the live board', () => {
  test('a stream that falls silent past the keep-alive is reconnected, and says so', async () => {
    const { calls } = serve({ sessions: [session(1)], prompts: {} })
    open()
    expect(await screen.findByText('Connecting to live updates…')).toBeInTheDocument()
    const streams = () => calls.filter((url) => url.pathname === '/api/stream').length
    expect(streams()).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SILENCE_MS + 6000)
    })
    expect(await screen.findByText(/Live updates stopped; reconnecting/)).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(streams()).toBeGreaterThan(1)
  })

  test('starting and ending a session change the board without a reload', async () => {
    const state = { sessions: [session(1, { title: 'Prune the trees' })], prompts: { 1: 'Sharpen the shears first' } }
    const { stream } = serve(state)
    open()

    expect(await screen.findByRole('heading', { level: 3, name: 'Prune the trees' })).toBeInTheDocument()
    expect(await screen.findByText('Sharpen the shears first')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('1 session live: 1 active.')
    act(() => stream.send('hello', { from: {} }))
    expect(await screen.findByText('Updates as sessions start, change and end.')).toBeInTheDocument()

    // A new session starts.
    state.sessions = [...state.sessions, session(2, { title: 'Plan next season', last_activity_at: ago(0) })]
    act(() => stream.send('sessions', { sessions: [{ id: 2 }] }))
    expect(await screen.findByRole('heading', { level: 3, name: 'Plan next season' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('2 sessions live: 2 active.')

    // The first one ends: it moves to "Just ended".
    state.sessions = [
      session(1, { title: 'Prune the trees', state: 'ended', ended_at: ago(0), state_basis: 'process_exited' }),
      state.sessions[1],
    ]
    act(() => stream.send('sessions', { sessions: [{ id: 1 }] }))
    const ended = await screen.findByRole('region', { name: 'Just ended' })
    expect(within(ended).getByRole('heading', { level: 3, name: 'Prune the trees' })).toBeInTheDocument()
    expect(within(ended).getByText('Process exited')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('1 session live: 1 active.'))
  })

  test('a new prompt refreshes the latest-prompt line', async () => {
    const state = { sessions: [session(1)], prompts: { 1: 'First ask' } as Record<number, string> }
    const { stream } = serve(state)
    open()
    expect(await screen.findByText('First ask')).toBeInTheDocument()
    state.prompts[1] = 'Second ask'
    act(() => stream.send('events', { sessions: [{ id: 1, newest_sequence: 40, new_events: 1 }] }))
    expect(await screen.findByText('Second ask')).toBeInTheDocument()
  })

  test('sessions started by another are nested under it', async () => {
    serve({
      sessions: [
        session(1, { title: 'Lead session' }),
        session(2, { title: 'Helper session', harness: 'codex', parent_session_id: 1, cwd: '/elsewhere' }),
      ],
      prompts: {},
    })
    open()
    const nested = await screen.findByRole('list', { name: 'Sessions started by Lead session' })
    expect(within(nested).getByRole('heading', { level: 3, name: 'Helper session' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(2)
  })

  test('unknown is shown as unknown, not as ended', async () => {
    serve({
      sessions: [session(1, { title: 'Quiet one', state: 'unknown', state_basis: 'no_evidence', last_activity_at: ago(12) })],
      prompts: {},
    })
    open()
    const row = (await screen.findByRole('heading', { level: 3, name: 'Quiet one' })).closest('li')!
    expect(within(row).getByText('Unknown')).toBeInTheDocument()
    expect(within(row).queryByText('Ended')).not.toBeInTheDocument()
    expect(within(row).getByText('No evidence of its state')).toBeInTheDocument()
    expect(within(row).getByText('Last active 12 minutes ago')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Just ended' })).not.toBeInTheDocument()
  })

  test('grouping by harness lives in the address and shows each folder on its row', async () => {
    serve({ sessions: [session(1), session(2, { harness: 'pi', cwd: '/work/meadow' })], prompts: {} })
    open()
    fireEvent.click(await screen.findByRole('radio', { name: 'Harness' }))
    expect(window.location.hash).toContain('group=harness')
    expect(await screen.findByRole('region', { name: /Pi/ })).toBeInTheDocument()
    expect(screen.getByText('/work/meadow')).toBeInTheDocument()
  })

  test('nothing running says so', async () => {
    serve({ sessions: [], prompts: {} })
    open()
    expect(await screen.findByRole('heading', { name: 'Nothing is running' })).toBeInTheDocument()
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
  })
})
