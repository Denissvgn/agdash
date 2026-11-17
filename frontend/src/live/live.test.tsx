import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { unnamedControls } from '../test/controls'
import { newQueryClient } from '../api/queryClient'
import type { LiveActivity, Page, SessionSummary } from '../api/types'
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

function serve(state: { sessions: SessionSummary[]; prompts: Record<number, string>; activity?: LiveActivity }) {
  const stream = new Stream()
  const calls: URL[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      calls.push(url)
      if (url.pathname === '/api/stream') return new Response(stream.open(init?.signal), { status: 200 })
      if (url.pathname === '/api/live/activity' && state.activity) return new Response(JSON.stringify(state.activity))
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

/** The board's own count of live sessions; the catching-up announcement comes before it. */
const summary = () => screen.getAllByRole('status').at(-1)!
/** A session's row on the board, found by its link. */
const rowOf = async (title: string) => (await screen.findByRole('link', { name: title })).closest('li')!
const panel = () => screen.getByRole('complementary', { name: 'Session details' })
const titles = () =>
  [...document.querySelectorAll('[data-roster] a[data-row]')].map((link) => link.textContent)

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
    // Said beside the rows, not only under "About this board".
    const warning = (await screen.findAllByText(/Live updates stopped; reconnecting/)).find((line) => !line.closest('details'))
    expect(warning).toBeDefined()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(streams()).toBeGreaterThan(1)
  })

  test('starting and ending a session change the board without a reload', async () => {
    const state = { sessions: [session(1, { title: 'Prune the trees' })], prompts: { 1: 'Sharpen the shears first' } }
    const { stream } = serve(state)
    open()

    await rowOf('Prune the trees')
    // The panel shows the first session until another is picked.
    expect(await within(panel()).findByText('Sharpen the shears first')).toBeInTheDocument()
    expect(summary()).toHaveTextContent('1 session live: 1 active.')
    act(() => stream.send('hello', { from: {} }))
    expect(await screen.findByText('Updates as sessions start, change and end.')).toBeInTheDocument()

    // A new session starts.
    state.sessions = [...state.sessions, session(2, { title: 'Plan next season', last_activity_at: ago(0) })]
    act(() => stream.send('sessions', { sessions: [{ id: 2 }] }))
    await rowOf('Plan next season')
    expect(summary()).toHaveTextContent('2 sessions live: 2 active.')

    // The first one ends: it moves to "Just ended".
    state.sessions = [
      session(1, { title: 'Prune the trees', state: 'ended', ended_at: ago(0), state_basis: 'process_exited' }),
      state.sessions[1],
    ]
    act(() => stream.send('sessions', { sessions: [{ id: 1 }] }))
    const ended = await screen.findByRole('region', { name: /Just ended/ })
    fireEvent.click(within(ended).getByRole('link', { name: 'Prune the trees' }))
    expect(await within(panel()).findByText('Process exited')).toBeInTheDocument()
    await waitFor(() => expect(summary()).toHaveTextContent('1 session live: 1 active.'))
  })

  test('a new prompt refreshes the latest prompt in the panel', async () => {
    const state = { sessions: [session(1)], prompts: { 1: 'First ask' } as Record<number, string> }
    const { stream } = serve(state)
    open()
    expect(await screen.findByText('First ask')).toBeInTheDocument()
    state.prompts[1] = 'Second ask'
    act(() => stream.send('events', { sessions: [{ id: 1, newest_sequence: 40, new_events: 1 }] }))
    expect(await screen.findByText('Second ask')).toBeInTheDocument()
  })

  test('only the session in the panel has its latest prompt fetched', async () => {
    const { calls } = serve({ sessions: [session(1), session(2), session(3)], prompts: { 1: 'One', 2: 'Two', 3: 'Three' } })
    open()
    await rowOf('Synthetic session 1')
    await within(panel()).findByText(/One|Two|Three/)
    expect(calls.filter((url) => url.pathname.endsWith('/events'))).toHaveLength(1)
  })

  test('a row is one line: state, title, harness, and how long ago in short and in words', async () => {
    serve({ sessions: [session(1, { title: 'Prune the trees', last_activity_at: ago(12) })], prompts: {} })
    open()
    const row = await rowOf('Prune the trees')
    expect(row).toHaveTextContent('Active')
    expect(row).toHaveTextContent('Claude Code')
    expect(within(row).getByText('12m')).toHaveAttribute('aria-hidden', 'true')
    expect(within(row).getByText(/Last active 12 minutes ago/)).toHaveClass('sr-only')
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('picking a row puts the session in the address, and the panel follows', async () => {
    serve({
      sessions: [session(1, { title: 'First one', last_activity_at: ago(0) }), session(2, { title: 'Second one', cwd: '/work/meadow' })],
      prompts: {},
    })
    open()
    const first = await rowOf('First one')
    expect(first).toHaveAttribute('data-selected', 'true')
    expect(within(panel()).getByRole('heading', { level: 2, name: 'First one' })).toBeInTheDocument()
    expect(window.location.hash).not.toContain('session=')

    fireEvent.click(screen.getByRole('link', { name: 'Second one' }))
    expect(window.location.hash).toContain('session=2')
    expect(await within(panel()).findByRole('heading', { level: 2, name: 'Second one' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Second one' })).toHaveAttribute('aria-current', 'true')
    expect(within(panel()).getByText('/work/meadow')).toBeInTheDocument()
    expect(within(panel()).getByRole('link', { name: 'Open session' })).toHaveAttribute('href', '#/history/sessions/2')

    // Up and down move through the rows and take the panel along.
    const second = screen.getByRole('link', { name: 'Second one' })
    second.focus()
    fireEvent.keyDown(second, { key: 'ArrowUp' })
    expect(screen.getByRole('link', { name: 'First one' })).toHaveFocus()
    await waitFor(() => expect(window.location.hash).toContain('session=1'))
    expect(within(panel()).getByRole('heading', { level: 2, name: 'First one' })).toBeInTheDocument()
  })

  test('sessions started by another sit under it, and the panel names both ways', async () => {
    serve({
      sessions: [
        session(1, { title: 'Lead session' }),
        session(2, { title: 'Helper session', harness: 'codex', parent_session_id: 1, parent_basis: 'process_tree', cwd: '/elsewhere' }),
        session(3, { title: 'Sub-agent', parent_session_id: 1, parent_basis: 'harness_record' }),
        session(4, { title: 'Alone', last_activity_at: ago(20) }),
      ],
      prompts: {},
    })
    open()
    await rowOf('Lead session')
    // Each under the session that started it, before the next session standing alone.
    expect(titles()).toEqual(['Lead session', 'Sub-agent', 'Helper session', 'Alone'])
    const lead = within(panel())
    expect(lead.getByText('Started 2 sessions')).toBeInTheDocument()

    // An edge between harnesses names the harness and its basis; one within a harness, its basis.
    fireEvent.click(lead.getByRole('link', { name: 'Helper session' }))
    expect(await within(panel()).findByText('Started from Claude Code, known from the process tree')).toBeInTheDocument()
    expect(within(panel()).getByRole('link', { name: 'Lead session' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: 'Sub-agent' }))
    expect(await within(panel()).findByText("Known from the harness's record")).toBeInTheDocument()
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('unknown is shown as unknown, not as ended', async () => {
    serve({
      sessions: [session(1, { title: 'Quiet one', state: 'unknown', state_basis: 'no_evidence', last_activity_at: ago(12) })],
      prompts: {},
    })
    open()
    const row = await rowOf('Quiet one')
    expect(within(row).getByText('Unknown')).toBeInTheDocument()
    expect(within(row).queryByText('Ended')).not.toBeInTheDocument()
    expect(within(panel()).getByText('No evidence of its state')).toBeInTheDocument()
    expect(within(panel()).getByText('Last active 12 minutes ago')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /Just ended/ })).not.toBeInTheDocument()
  })

  test('grouping by harness lives in the address', async () => {
    serve({ sessions: [session(1), session(2, { harness: 'pi', cwd: '/work/meadow' })], prompts: {} })
    open()
    fireEvent.click(await screen.findByRole('radio', { name: 'Harness' }))
    expect(window.location.hash).toContain('group=harness')
    expect(await screen.findByRole('region', { name: /Pi/ })).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: 'Group by' })).toBeInTheDocument()
  })

  test('the counts filter the board by state, and the choice lives in the address', async () => {
    serve({
      sessions: [
        session(1, { title: 'Lead session' }),
        session(2, { title: 'Its idle helper', state: 'idle', parent_session_id: 1 }),
        session(3, { title: 'Waiting one', state: 'waiting' }),
      ],
      prompts: {},
    })
    open()
    await rowOf('Lead session')
    const filters = within(screen.getByRole('group', { name: 'Show sessions by state' }))
    expect(filters.getByText('3 sessions live')).toBeInTheDocument()
    fireEvent.click(filters.getByRole('button', { name: '1 idle' }))
    expect(window.location.hash).toContain('show=idle')
    // The session it sits under stays, so the row still says where it belongs.
    await waitFor(() => expect(titles()).toEqual(['Lead session', 'Its idle helper']))
    expect(filters.getByRole('button', { name: '1 idle' })).toHaveAttribute('aria-pressed', 'true')
    expect(summary()).toHaveTextContent('3 sessions live')
    fireEvent.click(filters.getByRole('button', { name: '1 idle' }))
    await waitFor(() => expect(titles()).toHaveLength(3))
    expect(window.location.hash).not.toContain('show=')
  })

  test('sessions left open for over a day are folded into one line', async () => {
    serve({
      sessions: [
        session(1, { title: 'Working now' }),
        session(2, { title: 'Left open', state: 'idle', last_activity_at: ago(60 * 30) }),
        session(3, { title: 'Idle today', state: 'idle', last_activity_at: ago(60 * 3) }),
      ],
      prompts: {},
    })
    open()
    await rowOf('Working now')
    const fold = document.querySelector('details[data-quiet]')!
    expect(fold).not.toHaveAttribute('open')
    expect(within(fold as HTMLElement).getByText('1 session open but quiet for over a day')).toBeInTheDocument()
    expect((await rowOf('Left open')).closest('details')).toBe(fold)
    expect((await rowOf('Idle today')).closest('details')).toBeNull()
    // Still counted: it is open.
    expect(summary()).toHaveTextContent('3 sessions live: 1 active · 2 idle.')
  })

  test('past twenty rows the board tightens them', async () => {
    serve({ sessions: Array.from({ length: 21 }, (_, i) => session(i + 1, { title: `Session ${i + 1}` })), prompts: {} })
    open()
    expect((await rowOf('Session 1')).className).toContain('h-[24px]')
    cleanup()
    serve({ sessions: [session(1, { title: 'Only one' })], prompts: {} })
    open()
    expect((await rowOf('Only one')).className).toContain('h-[28px]')
  })

  test('the agents view draws the same sessions as a graph, with how each link is known', async () => {
    serve({
      sessions: [
        session(1, { title: 'Lead session' }),
        session(2, { title: 'Helper session', harness: 'codex', parent_session_id: 1, parent_basis: 'process_tree' }),
        session(3, { title: 'Sub-agent', parent_session_id: 1, parent_basis: 'harness_record' }),
      ],
      prompts: {},
    })
    open()
    fireEvent.click(await screen.findByRole('radio', { name: 'Agents' }))
    expect(window.location.hash).toContain('view=agents')
    const graph = document.querySelector('[data-agents]')!
    await waitFor(() => expect(graph.querySelectorAll('a[data-row]')).toHaveLength(3))
    expect([...graph.querySelectorAll('a[data-row]')].map((node) => node.getAttribute('data-session'))).toEqual(['1', '3', '2'])
    expect(graph.querySelector('path[data-edge="process_tree"]')).toHaveAttribute('stroke-dasharray')
    expect(graph.querySelector('path[data-edge="harness_record"]')).not.toHaveAttribute('stroke-dasharray')
    expect(within(graph as HTMLElement).getByRole('link', { name: /Helper session/ })).toHaveTextContent(
      'Started by Lead session, known from the process tree',
    )
    expect(graph.querySelector('[data-agents-key]')).toHaveTextContent('Recorded by the harnessKnown from the process tree')
    // A node is the same link as a row: picking it fills the panel.
    fireEvent.click(within(graph as HTMLElement).getByRole('link', { name: /Sub-agent/ }))
    expect(await within(panel()).findByRole('heading', { level: 2, name: 'Sub-agent' })).toBeInTheDocument()
    expect(window.location.hash).toContain('view=agents')
  })

  test('a row\'s link is named by its title and described by its state and times', async () => {
    serve({ sessions: [session(1, { title: 'Prune the trees', state: 'waiting', last_activity_at: ago(3) })], prompts: {} })
    open()
    const link = await screen.findByRole('link', { name: 'Prune the trees' })
    expect(link).toHaveAccessibleDescription(/^Waiting Last active 3 minutes ago/)
  })

  test('where the panel takes the rows\' place, focus goes to it and comes back', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('max-width: 1023px') }))
    serve({ sessions: [session(1, { title: 'First one', last_activity_at: ago(0) }), session(2, { title: 'Second one' })], prompts: {} })
    open()
    fireEvent.click(await screen.findByRole('link', { name: 'Second one' }))
    await waitFor(() => expect(within(panel()).getByRole('heading', { level: 2, name: 'Second one' })).toHaveFocus())
    fireEvent.click(within(panel()).getByRole('link', { name: 'All live sessions' }))
    await waitFor(() => expect(screen.getByRole('link', { name: 'Second one' })).toHaveFocus())
    expect(window.location.hash).not.toContain('session=')
  })

  test('nothing running says so', async () => {
    serve({ sessions: [], prompts: {} })
    open()
    expect(await screen.findByRole('heading', { name: 'Nothing is running' })).toBeInTheDocument()
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: 'Session details' })).not.toBeInTheDocument()
  })
})

describe('the last 30 minutes', () => {
  const minutes = Array.from({ length: 30 }, (_, i) => new Date(NOW - (30 - i) * 60_000).toISOString())
  const activity = (busy: number[], states: LiveActivity['sessions'][number]['states']): LiveActivity => ({
    minutes,
    until: new Date(NOW).toISOString(),
    sessions: [
      { id: 1, harness: 'claude_code', state: 'active', events: busy, total: busy.reduce((a, b) => a + b, 0), states },
      {
        id: 2,
        harness: 'pi',
        state: 'idle',
        events: Array(30).fill(0),
        total: 0,
        states: [{ state: 'idle', start: minutes[0], end: new Date(NOW).toISOString() }],
      },
    ],
    omitted: 0,
  })
  const span = (state: string, from: number, to: number) => ({
    state: state as 'active',
    start: minutes[from],
    end: to >= 30 ? new Date(NOW).toISOString() : minutes[to],
  })
  const states = (within: Element, strip: string) =>
    [...within.querySelectorAll(`[data-strip="${strip}"] [data-state]`)].map((r) => r.getAttribute('data-state'))

  test('each row draws them as one lane, and they change with the stream without a reload or motion', async () => {
    const busy = Array(30).fill(0)
    busy[28] = 3
    busy[29] = 2
    const state = {
      sessions: [session(1, { title: 'Busy one' }), session(2, { title: 'Quiet one', harness: 'pi', state: 'idle' as const })],
      prompts: {},
      activity: activity(busy, [span('waiting', 0, 20), span('active', 20, 30)]),
    }
    const { stream, calls } = serve(state)
    open()
    const row = await rowOf('Busy one')
    await waitFor(() => expect(row.querySelector('[data-strip="lane"]')).not.toBeNull())
    // Hidden from assistive technology, and said in words in the row.
    expect(row.querySelector('[data-strip="lane"]')!.closest('[aria-hidden="true"]')).not.toBeNull()
    expect(row.querySelectorAll('[data-strip="lane"] > rect')).toHaveLength(30)
    expect(states(row, 'lane')).toEqual(['waiting', 'active'])
    expect(row).toHaveTextContent('Last 30 minutes: 5 events; Waiting for 20 min, then Active for 10 min.')
    expect(screen.getByRole('list', { name: 'Legend' })).toHaveTextContent('Events per minute, darker for more')
    // Every lane is on the same clock: the same width, under one pair of labels.
    const lanes = [...document.querySelectorAll('[data-strip="lane"]')]
    expect(new Set(lanes.map((lane) => lane.getAttribute('width'))).size).toBe(1)
    expect(document.querySelector('[data-roster-head]')).toHaveTextContent('30 min agonow')
    // The panel has them at full size, with the count.
    expect(within(panel()).getByText('5 in 30 min')).toBeInTheDocument()
    expect(states(panel(), 'states')).toEqual(['waiting', 'active'])
    expect(panel().querySelector('[data-strip-words]')).toHaveTextContent('Waiting for 20 min, then Active for 10 min')
    // The key names only the states some lane draws.
    expect(screen.getByRole('list', { name: 'Legend' })).not.toHaveTextContent('Ended')

    // The stream says a minute passed and the session wrote: the lane changes in place.
    const reloads = calls.filter((url) => url.pathname === '/api/sessions').length
    busy[29] = 6
    state.activity = activity(busy, [span('waiting', 0, 20), span('active', 20, 25), span('idle', 25, 30)])
    act(() => stream.send('activity', { minute: new Date(NOW).toISOString(), sessions: [1] }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400)
    })
    await waitFor(() => expect(within(panel()).getByText('9 in 30 min')).toBeInTheDocument())
    expect(states(row, 'lane')).toEqual(['waiting', 'active', 'idle'])
    expect(calls.filter((url) => url.pathname === '/api/sessions').length).toBe(reloads)
    for (const element of document.querySelectorAll('[data-strip] , [data-strip] *')) {
      expect(element.getAttribute('class') ?? '').not.toMatch(/\b(transition|animate-|duration-)/)
    }
    expect(document.querySelectorAll('[data-roster] animate, [data-roster] animateTransform, [data-roster] set')).toHaveLength(0)
  })

  test('a session that wrote nothing in the window says so in the panel instead of drawing empty cells', async () => {
    serve({
      sessions: [session(1, { title: 'Busy one' }), session(2, { title: 'Quiet one', harness: 'pi', state: 'idle' })],
      prompts: {},
      activity: activity(Array(30).fill(1), [span('active', 0, 30)]),
    })
    open()
    const quiet = await rowOf('Quiet one')
    await waitFor(() => expect(states(quiet, 'lane')).toEqual(['idle']))
    expect(quiet).toHaveTextContent('Last 30 minutes: 0 events; Idle for 30 min.')
    fireEvent.click(screen.getByRole('link', { name: 'Quiet one' }))
    expect(await within(panel()).findByText('No events in the last 30 minutes')).toBeInTheDocument()
    expect(panel().querySelector('[data-strip="events"]')).toBeNull()
    expect(panel().querySelector('[data-strip="states"] [data-state="idle"]')).not.toBeNull()
  })
})
