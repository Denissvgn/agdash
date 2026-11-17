import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { unnamedControls } from '../test/controls'
import { newQueryClient } from '../api/queryClient'
import type { Facets, Page, SessionHit, SessionSummary } from '../api/types'
import { apiParams, isFiltered, NO_FILTERS, readFilters, writeFilters } from './filters'

// Synthetic sessions: no real transcript content appears in tests.
const session = (id: number, patch: Partial<SessionSummary> = {}): SessionSummary => ({
  id,
  harness: 'codex',
  native_id: `native-${id}`,
  surface: 'cli',
  title: `Synthetic session ${id}`,
  cwd: `/work/example/${id}`,
  branch: null,
  state: 'ended',
  started_at: '2026-11-10T09:00:00Z',
  last_activity_at: '2026-11-10T10:00:00Z',
  ended_at: null,
  models: ['model-a'],
  effort: 'high',
  parent_session_id: null,
  initial_task: 'Synthetic task',
  event_count: 12,
  child_count: 0,
  ...patch,
})

const FACETS: Facets = {
  harnesses: [
    { value: 'codex', count: 2 },
    { value: 'pi', count: 1 },
  ],
  surfaces: [{ value: 'cli', count: 3 }],
  states: [{ value: 'ended', count: 3 }],
  models: [
    { value: 'model-a', count: 2 },
    { value: 'model-b', count: 1 },
  ],
  folders: [{ value: '/work/example/1', count: 1 }],
  auth_methods: [],
  first_started_at: '2026-11-01T00:00:00Z',
  last_started_at: '2026-11-10T09:00:00Z',
}

// Sessions started per day, 1 to 10 November 2026: synthetic counts.
const STARTED = {
  time_zone: 'UTC',
  days: [3, 0, 1, 4, 2, 0, 0, 5, 1, 2].map((sessions, i) => ({ day: `2026-11-${String(i + 1).padStart(2, '0')}`, sessions })),
  undated: 0,
}

type Handler = (url: URL) => { status?: number; body?: unknown } | 'offline'

function serve(handler: Handler) {
  const calls: URL[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      calls.push(url)
      const answer =
        url.pathname === '/api/sessions/facets'
          ? { body: FACETS }
          : url.pathname === '/api/analytics/started'
            ? { body: STARTED }
            : handler(url)
      if (answer === 'offline') throw new TypeError('network down')
      return new Response(JSON.stringify(answer.body ?? {}), { status: answer.status ?? 200 })
    }),
  )
  return calls
}

const page = <T,>(items: T[], next: string | null = null): Page<T> => ({ items, next_cursor: next })

function open(hash: string) {
  window.location.hash = hash
  render(<App queryClient={newQueryClient()} />)
}

const address = () => new URLSearchParams(window.location.hash.split('?')[1] ?? '')

beforeEach(() => {
  window.location.hash = ''
})

describe('filters in the address', () => {
  test('read and written back unchanged, with other parameters kept', () => {
    const search = new URLSearchParams('q=parser&harness=codex&harness=pi&model=m&from=2026-11-01&to=bad')
    const filters = readFilters(search)
    expect(filters).toEqual({ harness: ['codex', 'pi'], model: 'm', folder: '', auth: '', from: '2026-11-01', to: '', range: '' })
    const written = writeFilters(search, filters)
    expect(written.get('q')).toBe('parser')
    expect(written.getAll('harness')).toEqual(['codex', 'pi'])
    expect(written.has('to')).toBe(false)
    expect(isFiltered(NO_FILTERS)).toBe(false)
  })

  test('local days become instants, the end day included', () => {
    const params = apiParams({ ...NO_FILTERS, from: '2026-11-01', to: '2026-11-03' })
    expect(params.started_from).toBe(new Date(2026, 10, 1).toISOString())
    expect(params.started_before).toBe(new Date(2026, 10, 4).toISOString())
  })
})

describe('the sessions list', () => {
  test('shows recorded sessions', async () => {
    const calls = serve(() => ({
      body: page([
        session(1, { state: 'ended' }),
        session(2, { title: null, initial_task: null, state: 'active', child_count: 2 }),
        session(3, { title: null, initial_task: '<note kind="synthetic">\n\n  Prune   the orchard rows\nand then the hedge', state: 'unknown' }),
      ]),
    }))
    open('#/history')
    const list = await screen.findByRole('list', { name: 'Sessions' })
    expect(within(list).getByText('Synthetic session 1')).toBeInTheDocument()
    expect(within(list).getByText('Untitled session')).toBeInTheDocument()
    // With no title, a session is called by how its first prompt began, without markup.
    expect(within(list).getByRole('link', { name: 'Prune the orchard rows' })).toBeInTheDocument()
    expect(within(list).getAllByText(/Last active/)).toHaveLength(3)
    // A state that says something is shown; "Unknown" on a past session is not.
    expect(within(list).getByText('Active')).toBeInTheDocument()
    expect(within(list).getByText('Ended')).toBeInTheDocument()
    expect(within(list).queryByText('Unknown')).not.toBeInTheDocument()
    expect(within(list).getByText('2 subagents')).toBeInTheDocument()
    expect(within(list).getAllByText('Codex · CLI')).toHaveLength(3)
    expect(unnamedControls(document.body)).toEqual([])
    // Top-level sessions with something recorded: subagents are counted on their parent's row.
    const asked = calls.find((url) => url.pathname === '/api/sessions')!
    expect(asked.searchParams.get('roots_only')).toBe('true')
    expect(asked.searchParams.get('recorded')).toBe('true')
  })

  test('the harness and the period are always in view; the rest sits behind More filters', async () => {
    serve(() => ({ body: page([session(1)]) }))
    open('#/history')
    await screen.findByRole('list', { name: 'Sessions' })
    const filters = within(screen.getByRole('region', { name: 'Filters' }))
    expect(within(filters.getByRole('radiogroup', { name: 'Started' })).getAllByRole('radio')).toHaveLength(4)
    const more = filters.getByRole('button', { name: 'More filters' })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    const panel = document.getElementById(more.getAttribute('aria-controls')!)!
    expect(panel).toHaveAttribute('hidden')
    await act(async () => fireEvent.click(more))
    expect(more).toHaveAttribute('aria-expanded', 'true')
    expect(panel).not.toHaveAttribute('hidden')
    // The button says how many filters are set behind it, open or closed, and always toggles.
    await act(async () => fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'model-b' } }))
    await act(async () =>
      fireEvent.change(screen.getByLabelText('Started on or after'), { target: { value: '2026-11-02' } }),
    )
    await act(async () =>
      fireEvent.change(screen.getByLabelText('Started on or before'), { target: { value: '2026-11-09' } }),
    )
    // The two dates are one filter.
    const set = filters.getByRole('button', { name: 'More filters (2 set)' })
    expect(set).toHaveAttribute('aria-expanded', 'true')
    await act(async () => fireEvent.click(set))
    expect(set).toHaveAttribute('aria-expanded', 'false')
    expect(panel).toHaveAttribute('hidden')
  })

  test('a filter already in the address opens More filters by itself', async () => {
    serve(() => ({ body: page([session(1)]) }))
    open('#/history?model=model-b')
    await screen.findByRole('list', { name: 'Sessions' })
    expect(screen.getByRole('button', { name: 'More filters (1 set)' })).toHaveAttribute('aria-expanded', 'true')
  })

  test('filters combine, are written to the address and sent to the service', async () => {
    const calls = serve(() => ({ body: page([session(1)]) }))
    open('#/history')
    await screen.findByRole('list', { name: 'Sessions' })
    await act(async () => fireEvent.click(await screen.findByRole('button', { name: /Codex/ })))
    await act(async () => fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'model-b' } }))
    await act(async () =>
      fireEvent.change(screen.getByLabelText('Started on or after'), { target: { value: '2026-11-02' } }),
    )
    expect(address().getAll('harness')).toEqual(['codex'])
    expect(address().get('model')).toBe('model-b')
    expect(address().get('from')).toBe('2026-11-02')
    await waitFor(() => {
      const last = calls.filter((url) => url.pathname === '/api/sessions').at(-1)!
      expect(last.searchParams.getAll('harness')).toEqual(['codex'])
      expect(last.searchParams.get('model')).toBe('model-b')
      expect(last.searchParams.get('started_from')).toBe(new Date(2026, 10, 2).toISOString())
    })
    expect(screen.getByRole('button', { name: /Codex/ })).toHaveAttribute('aria-pressed', 'true')
  })

  test('an address with filters opens with them applied', async () => {
    const calls = serve(() => ({ body: page([session(1)]) }))
    open('#/history?harness=pi&model=model-a&folder=%2Fwork%2Fexample%2F1')
    await screen.findByRole('list', { name: 'Sessions' })
    expect(screen.getByLabelText('Model')).toHaveValue('model-a')
    expect(screen.getByLabelText('Folder')).toHaveValue('/work/example/1')
    expect(await screen.findByRole('button', { name: /Pi/ })).toHaveAttribute('aria-pressed', 'true')
    const sent = calls.find((url) => url.pathname === '/api/sessions')!
    expect(sent.searchParams.getAll('harness')).toEqual(['pi'])
    expect(sent.searchParams.get('folder')).toBe('/work/example/1')
  })

  test('a date preset narrows the list to the last days, by the address', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 10, 16, 12))
    try {
      const calls = serve(() => ({ body: page([session(1)]) }))
      open('#/history')
      await screen.findByRole('list', { name: 'Sessions' })
      expect(screen.getByRole('radio', { name: 'All time' })).toBeChecked()
      await act(async () => fireEvent.click(screen.getByRole('radio', { name: 'Last 90 days' })))
      expect(address().get('range')).toBe('90')
      await waitFor(() => {
        const sent = calls.filter((url) => url.pathname === '/api/sessions').at(-1)!
        expect(sent.searchParams.get('started_from')).toBe(new Date(2026, 7, 19).toISOString())
      })
    } finally {
      vi.useRealTimers()
    }
  })

  test('clearing the filters empties the address', async () => {
    serve(() => ({ body: page([]) }))
    open('#/history?harness=pi&model=model-a')
    expect(await screen.findByText('No sessions match these filters')).toBeInTheDocument()
    await act(async () => fireEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]))
    expect(address().toString()).toBe('')
    expect(await screen.findByText('No sessions recorded yet')).toBeInTheDocument()
  })

  test('more pages load on request', async () => {
    serve((url) =>
      url.searchParams.get('cursor') ? { body: page([session(3)]) } : { body: page([session(1), session(2)], 'next') },
    )
    open('#/history')
    await act(async () => fireEvent.click(await screen.findByRole('button', { name: 'Show more' })))
    expect(await screen.findByText('Synthetic session 3')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument()
  })

  test.each([
    ['offline' as const, "Can't reach the agdash service"],
    [{ status: 401 }, 'The service refused this request'],
    [{ status: 500 }, 'The service could not answer'],
  ])('a failure is explained and can be retried (%o)', async (failure, title) => {
    let failing = true
    serve(() => (failing ? failure : { body: page([session(1)]) }))
    open('#/history')
    expect(await screen.findByText(title, {}, { timeout: 3000 })).toBeInTheDocument()
    failing = false
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Try again' })))
    expect(await screen.findByText('Synthetic session 1')).toBeInTheDocument()
  })
})

describe('search', () => {
  const hit = (id: number): SessionHit => ({
    ...session(id),
    hits: 3,
    excerpts: [
      {
        event_id: id * 10,
        sequence: 0,
        kind: 'user_prompt',
        occurred_at: null,
        snippet: [
          { text: 'fix the ', match: false },
          { text: 'parser', match: true },
          { text: ' before release', match: false },
        ],
      },
    ],
  })

  test('without a query it says what to type', () => {
    serve(() => ({ body: page([]) }))
    open('#/history/search')
    expect(screen.getByText('Search your history')).toBeInTheDocument()
  })

  test('returns matching sessions with the matches highlighted', async () => {
    const calls = serve(() => ({ body: page([hit(1), hit(2)]) }))
    open('#/history/search?q=parser')
    const results = await screen.findByRole('list', { name: 'Sessions matching “parser”' })
    const marks = results.querySelectorAll('mark')
    expect(marks).toHaveLength(2)
    expect([...marks].map((mark) => mark.textContent)).toEqual(['parser', 'parser'])
    expect(within(results).getAllByText('3 matches')).toHaveLength(2)
    expect(within(results).getAllByText('Prompt')).toHaveLength(2)
    expect(calls.find((url) => url.pathname === '/api/search/sessions')!.searchParams.get('q')).toBe('parser')
    expect(unnamedControls(document.body)).toEqual([])
  })


  test('a query and filters combine in the address and in the request', async () => {
    const calls = serve(() => ({ body: page([hit(1)]) }))
    open('#/history/search')
    await act(async () =>
      fireEvent.change(screen.getByLabelText('Search sessions'), { target: { value: 'parser bug' } }),
    )
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Search' })))
    await act(async () => fireEvent.click(await screen.findByRole('button', { name: /Pi/ })))
    expect(address().get('q')).toBe('parser bug')
    expect(address().getAll('harness')).toEqual(['pi'])
    await waitFor(() => {
      const last = calls.filter((url) => url.pathname === '/api/search/sessions').at(-1)!
      expect(last.searchParams.get('q')).toBe('parser bug')
      expect(last.searchParams.getAll('harness')).toEqual(['pi'])
    })
  })

  test('says so when nothing matches', async () => {
    serve(() => ({ body: page([]) }))
    open('#/history/search?q=nothing')
    expect(await screen.findByRole('heading', { name: 'Nothing matches “nothing”' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Nothing matches “nothing”')
  })

  test('announces the results and drops counts that would not describe them', async () => {
    serve(() => ({ body: page([hit(1)]) }))
    open('#/history/search?q=parser')
    await screen.findByRole('list', { name: 'Sessions matching “parser”' })
    expect(screen.getByRole('status')).toHaveTextContent('Sessions matching “parser”')
    expect(await screen.findByRole('button', { name: 'Codex' })).toHaveAttribute('aria-pressed', 'false')
  })
})

test('a session with no recorded activity says so', async () => {
  serve(() => ({ body: page([session(9, { last_activity_at: null, started_at: null })]) }))
  open('#/history')
  expect(await screen.findByText('No activity recorded')).toBeInTheDocument()
})

describe('the overview of sessions started per day', () => {
  // Ten days of synthetic starts, 1 to 10 November; "today" is the last of them.
  const setup = async (hash = '#/history') => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 10, 10, 12))
    const calls = serve(() => ({ body: page([session(1)]) }))
    open(hash)
    const title = await screen.findByRole('heading', { name: 'Sessions started per day' })
    const figure = title.closest('figure') as HTMLElement
    await waitFor(() => expect(within(figure).getAllByRole('slider')).toHaveLength(2))
    const [start, end] = within(figure).getAllByRole('slider')
    const track = figure.querySelector<HTMLElement>('.touch-none')!
    const box = track.getBoundingClientRect()
    // The edges sit at the start of the first picked day and the end of the last.
    const [first, last] = [Number(start.getAttribute('aria-valuenow')), Number(end.getAttribute('aria-valuenow'))]
    const slot = (Number.parseFloat(end.style.left) - Number.parseFloat(start.style.left)) / (last + 1 - first)
    const left = Number.parseFloat(start.style.left) - first * slot
    // The middle of the day ``d`` of November, in the page's coordinates.
    const day = (d: number) => box.left + left + slot * (d - 1 + 0.5)
    const sent = () => calls.filter((url) => url.pathname === '/api/sessions').at(-1)!
    return { figure, track, day, sent, start, end }
  }
  afterEach(() => vi.useRealTimers())

  test('a dragged range sets Started between in the address, the dates and the list', async () => {
    const { track, day, sent } = await setup()
    await act(async () => {
      fireEvent.pointerDown(track, { clientX: day(3), pointerId: 1 })
      fireEvent.pointerMove(track, { clientX: day(5), pointerId: 1 })
      fireEvent.pointerUp(track, { clientX: day(5), pointerId: 1 })
    })
    expect([address().get('from'), address().get('to')]).toEqual(['2026-11-03', '2026-11-05'])
    expect(screen.getByLabelText('Started on or after')).toHaveValue('2026-11-03')
    expect(screen.getByLabelText('Started on or before')).toHaveValue('2026-11-05')
    await waitFor(() => {
      expect(sent().searchParams.get('started_from')).toBe(new Date(2026, 10, 3).toISOString())
      expect(sent().searchParams.get('started_before')).toBe(new Date(2026, 10, 6).toISOString())
    })
  })

  test('a click picks one day, and shift-click extends it', async () => {
    const { track, day } = await setup('#/history?from=2026-11-02&to=2026-11-03')
    await act(async () => {
      fireEvent.pointerDown(track, { clientX: day(7), pointerId: 1 })
      fireEvent.pointerUp(track, { clientX: day(7), pointerId: 1 })
    })
    expect([address().get('from'), address().get('to')]).toEqual(['2026-11-07', '2026-11-07'])
    await act(async () => {
      fireEvent.pointerDown(track, { clientX: day(9), pointerId: 1, shiftKey: true })
      fireEvent.pointerUp(track, { clientX: day(9), pointerId: 1, shiftKey: true })
    })
    expect([address().get('from'), address().get('to')]).toEqual(['2026-11-07', '2026-11-09'])
  })

  test('the keyboard moves an edge, or the whole range with Shift, and the list follows', async () => {
    const { start, end, sent } = await setup('#/history?from=2026-11-02&to=2026-11-04')
    expect(start).toHaveAttribute('aria-valuetext', 'Nov 2')
    await act(async () => fireEvent.keyDown(end, { key: 'ArrowRight' }))
    expect(address().get('to')).toBe('2026-11-05')
    await act(async () => fireEvent.keyDown(start, { key: 'ArrowRight', shiftKey: true }))
    expect([address().get('from'), address().get('to')]).toEqual(['2026-11-03', '2026-11-06'])
    await waitFor(() => expect(sent().searchParams.get('started_from')).toBe(new Date(2026, 10, 3).toISOString()))
    // Every day picked is no date filter at all.
    await act(async () => fireEvent.keyDown(start, { key: 'Home' }))
    await act(async () => fireEvent.keyDown(end, { key: 'End' }))
    expect(address().has('from') || address().has('to')).toBe(false)
  })

  test('Clear filters clears the picked range and the dates together', async () => {
    const { start, end } = await setup('#/history?from=2026-11-03&to=2026-11-05')
    expect([start.getAttribute('aria-valuenow'), end.getAttribute('aria-valuenow')]).toEqual(['2', '4'])
    await act(async () => fireEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]))
    expect(address().toString()).toBe('')
    expect(screen.getByLabelText('Started on or after')).toHaveValue('')
    const [cleared, through] = screen.getAllByRole('slider')
    expect([cleared.getAttribute('aria-valuenow'), through.getAttribute('aria-valuenow')]).toEqual(['0', '9'])
  })
})
