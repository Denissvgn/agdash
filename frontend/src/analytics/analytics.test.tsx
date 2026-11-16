import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { unnamedControls } from '../test/controls'
import { newQueryClient } from '../api/queryClient'
import type { ActivityReport, Facets, SeriesPoint, SeriesReport, ToolReport, UsageReport, UsageRow } from '../api/types'

// Synthetic figures only.
const FACETS: Facets = {
  harnesses: [{ value: 'pi', count: 2 }, { value: 'codex', count: 1 }],
  surfaces: [],
  states: [],
  models: [{ value: 'model-a', count: 1 }],
  folders: [],
  auth_methods: [],
  first_started_at: '2026-11-02T09:00:00Z',
  last_started_at: '2026-11-04T09:00:00Z',
}

const row = (patch: Partial<UsageRow>): UsageRow => ({
  key: 'pi',
  sessions: 2,
  sessions_with_usage: 1,
  messages: 3,
  input_tokens: 1200,
  output_tokens: 300,
  cache_read_tokens: 5000,
  cache_write_tokens: null,
  reasoning_tokens: null,
  reported_cost: 0.25,
  duration_seconds: 5400,
  sessions_with_duration: 1,
  reported_by: { input_tokens: 1, output_tokens: 1, cache_read_tokens: 1, cache_write_tokens: 0, reasoning_tokens: 0, reported_cost: 1 },
  ...patch,
})

const REPORT: UsageReport = {
  group: 'harness',
  rows: [
    row({}),
    row({
      key: 'codex',
      sessions: 1,
      sessions_with_usage: 0,
      messages: 0,
      input_tokens: null,
      output_tokens: null,
      cache_read_tokens: null,
      reported_cost: null,
      duration_seconds: null,
      sessions_with_duration: 0,
    }),
  ],
  total: row({ key: null, sessions: 3 }),
  sessions_without_usage: 2,
}

const TOOLS: ToolReport = {
  rows: [
    { name: 'bash', calls: 40, sessions: 2, outcomes_known: 10, errors: 3 },
    { name: 'read_file', calls: 7, sessions: 1, outcomes_known: 0, errors: 0 },
  ],
  total_calls: 47,
  total_sessions: 2,
  truncated: false,
  outcomes_known: 10,
  errors: 3,
}

const point = (patch: Partial<SeriesPoint>): SeriesPoint => ({
  input_tokens: null,
  output_tokens: null,
  cache_read_tokens: null,
  cache_write_tokens: null,
  reasoning_tokens: null,
  reported_cost: null,
  messages: 0,
  sessions: 0,
  active_minutes: 0,
  sessions_with_usage: 0,
  reported_by: {},
  ...patch,
})

// Three days, adding up to the ledger's total above: Pi reports on the first day in
// full, on the second from one of its two sessions; Codex is active on the third
// day and reports nothing.
const SERIES: SeriesReport = {
  bucket: 'day',
  group: 'harness',
  time_zone: 'UTC',
  // Local days, as the service makes them for the reader's time zone.
  buckets: [0, 1, 2].map((d) => ({
    start: new Date(2026, 10, 2 + d).toISOString(),
    end: new Date(2026, 10, 3 + d).toISOString(),
  })),
  series: [
    {
      key: 'pi',
      points: [
        point({ input_tokens: 1000, output_tokens: 200, cache_read_tokens: 5000, messages: 2, sessions: 1, sessions_with_usage: 1, reported_cost: 0.2, reported_by: { reported_cost: 1 } }),
        point({ input_tokens: 200, output_tokens: 100, messages: 1, sessions: 2, sessions_with_usage: 1, reported_cost: 0.05, reported_by: { reported_cost: 1 } }),
        point({}),
      ],
      total: point({ input_tokens: 1200, output_tokens: 300, cache_read_tokens: 5000, messages: 3, sessions: 2, sessions_with_usage: 1, reported_cost: 0.25 }),
    },
    {
      key: 'codex',
      points: [point({}), point({}), point({ sessions: 1 })],
      total: point({ sessions: 1 }),
    },
  ],
  total: {
    key: null,
    points: [],
    total: point({ input_tokens: 1200, output_tokens: 300, cache_read_tokens: 5000, messages: 3, sessions: 3, sessions_with_usage: 1, reported_cost: 0.25, reported_by: { reported_cost: 1 } }),
  },
  undated_usage: 0,
}

// Two days of synthetic activity: Pi on Monday morning, Codex on Tuesday afternoon.
const ACTIVITY: ActivityReport = {
  time_zone: 'UTC',
  bucket: 'hour',
  hour_of_week: Array.from({ length: 7 * 24 }, (_, i) => ({
    weekday: Math.floor(i / 24),
    hour: i % 24,
    session_hours: i === 9 ? 2 : i === 10 ? 1 : i === 24 + 15 ? 3 : 0,
  })),
  buckets: [0, 1, 2].map((h) => ({ start: new Date(2026, 10, 2, 9 + h).toISOString(), end: new Date(2026, 10, 2, 10 + h).toISOString() })),
  parallel: [
    { key: 'pi', sessions: [2, 1, 0] },
    { key: 'codex', sessions: [0, 0, 1] },
  ],
  total: { key: null, sessions: [2, 1, 1] },
}

function serve(
  usage: (url: URL) => UsageReport = () => REPORT,
  series: (url: URL) => SeriesReport = () => SERIES,
  activity: ActivityReport = ACTIVITY,
) {
  const calls: URL[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      calls.push(url)
      const body =
        url.pathname === '/api/sessions/facets'
          ? FACETS
          : url.pathname === '/api/analytics/tools'
            ? TOOLS
            : url.pathname === '/api/analytics/series'
              ? series(url)
              : url.pathname === '/api/analytics/activity'
                ? activity
                : usage(url)
      return new Response(JSON.stringify(body), { status: 200 })
    }),
  )
  return calls
}

function open(hash: string) {
  window.location.hash = hash
  render(<App queryClient={newQueryClient()} />)
}

beforeEach(() => {
  window.location.hash = ''
})

test('unreported numbers are a dash that says so, never a zero', async () => {
  serve()
  open('#/analytics')
  const table = await screen.findByRole('table', { name: 'Usage by Harness' })
  const codex = within(table).getByRole('row', { name: /Codex/ })
  const cells = within(codex).getAllByRole('cell')
  expect(cells[0]).toHaveTextContent('1')
  for (const cell of cells.slice(1)) {
    expect(cell).toHaveTextContent('—not reported')
    expect(cell.textContent).not.toMatch(/\b0\b/)
  }
  const pi = within(table).getByRole('row', { name: /^Pi/ })
  expect(within(pi).getAllByRole('cell')[4]).toHaveTextContent('—not reported') // cache write
  expect(pi).toHaveTextContent('1 with usage')
  expect(pi).toHaveTextContent('1 h 30 min')
  expect(pi).toHaveTextContent('$0.25')
  expect(screen.getByText(/A dash means the harness did not report/)).toBeInTheDocument()
  expect(screen.getByText('2 sessions reported no usage at all and are left out of the token figures.')).toBeInTheDocument()
  expect(unnamedControls(document.body)).toEqual([])
})


test('the totals row shows the report total', async () => {
  serve()
  open('#/analytics')
  const total = await screen.findByRole('row', { name: /All sessions/ })
  expect(total).toHaveTextContent('3')
  expect(total).toHaveTextContent('1,200')
})

test('grouping lives in the address and reaches the service', async () => {
  const calls = serve((url) => ({ ...REPORT, group: (url.searchParams.get('group') ?? 'harness') as UsageReport['group'] }))
  open('#/analytics?harness=pi')
  await screen.findByRole('table')
  await act(async () => fireEvent.click(screen.getByRole('radio', { name: 'Model' })))
  expect(new URLSearchParams(window.location.hash.split('?')[1]).get('group')).toBe('model')
  await waitFor(() => {
    const last = calls.filter((url) => url.pathname === '/api/analytics/usage').at(-1)!
    expect(last.searchParams.get('group')).toBe('model')
    expect(last.searchParams.getAll('harness')).toEqual(['pi'])
  })
  expect(await screen.findByText(/time is not split by model/)).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: 'Model' })).toBeChecked()
})

test('the tool mix shows calls and errors, with a dash where no outcome was matched', async () => {
  serve()
  open('#/analytics/tools')
  const table = await screen.findByRole('table', { name: 'Tool calls by tool' })
  const bash = within(table).getByRole('row', { name: /bash/ })
  expect(bash).toHaveTextContent('40')
  expect(bash).toHaveTextContent('of 10 matched')
  const read = within(table).getByRole('row', { name: /read_file/ })
  expect(within(read).getAllByRole('cell')[2]).toHaveTextContent('—not reported')
  expect(within(table).getByRole('row', { name: /All sessions/ })).toHaveTextContent('47')
  expect(unnamedControls(document.body)).toEqual([])
})

test('a sum built from only some sessions says how many it covers', async () => {
  const total = row({
    key: null,
    sessions: 20,
    sessions_with_usage: 15,
    reported_by: { input_tokens: 15, output_tokens: 15, cache_read_tokens: 15, cache_write_tokens: 4, reasoning_tokens: 2, reported_cost: 1 },
  })
  serve(() => ({ ...REPORT, total }))
  open('#/analytics')
  const row_ = await screen.findByRole('row', { name: /All sessions/ })
  const cells = within(row_).getAllByRole('cell')
  expect(cells[6]).toHaveTextContent('$0.25from 1 of 15') // cost
  expect(cells[4]).not.toHaveTextContent('from') // cache write is null here: a dash, no coverage line
  expect(cells[1]).not.toHaveTextContent('from') // input covers every session with usage
  expect(cells[7]).toHaveTextContent('from 1 of 20') // elapsed: one session with a known span
})

describe('usage over time', () => {
  // The figure once it has drawn: its caption ties it to the ledger.
  const figure = async (title = 'Tokens per day, by harness') => {
    await screen.findByText(/in all, the same total/)
    return screen.getByRole('heading', { name: title }).closest('figure') as HTMLElement
  }

  test('asks for the same sessions as the ledger, and its total is the ledger total', async () => {
    const calls = serve()
    open('#/analytics?harness=pi&from=2026-11-01&to=2026-11-03')
    const drawn = await figure()
    const usage = calls.find((url) => url.pathname === '/api/analytics/usage')!
    const series = calls.find((url) => url.pathname === '/api/analytics/series')!
    for (const key of ['harness', 'started_from', 'started_before', 'group']) {
      expect(series.searchParams.getAll(key)).toEqual(usage.searchParams.getAll(key))
    }
    expect(series.searchParams.get('bucket')).toBe('day')
    // 1,200 + 300 + 5,000 tokens, as the ledger's total row adds them up.
    expect(drawn).toHaveTextContent('6,500 in all, the same total as the table below.')
    const total = screen.getByRole('row', { name: /All sessions/ })
    const cells = within(total)
      .getAllByRole('cell')
      .slice(1, 6)
      .map((cell) => Number((cell.textContent ?? '').match(/^[\d,]+/)?.[0].replace(/,/g, '') ?? 0))
    expect(cells.reduce((a, b) => a + b, 0)).toBe(6500)
  })

  test('hatches what was not reported, and says "from n of m" where only some sessions reported', async () => {
    serve()
    open('#/analytics')
    const drawn = await figure()
    // Codex was active on the third day and reported nothing: hatched, never a zero column.
    const marks = [...drawn.querySelectorAll('[role="img"]')].map((mark) => mark.getAttribute('aria-label'))
    expect(marks).toContain('Codex, Nov 4: Not reported')
    expect(marks).toContain('Pi, Nov 3: 300, from 1 of 2')
    expect(drawn.querySelector('[data-coverage="1/2"]')).not.toBeNull()
    // Codex's third day: nothing reported, so it is hatched, with no coverage strip.
    expect(drawn.querySelector('[data-coverage="0/1"]')).toBeNull()
    const twin = within(drawn).getByRole('table')
    expect(within(twin).getByRole('row', { name: /Nov 3/ })).toHaveTextContent('300, from 1 of 2')
    expect(within(twin).getByRole('row', { name: /Nov 4/ })).toHaveTextContent('not reported')
    expect(drawn).toHaveTextContent('Reported by 1 of 3 sessions.')
    // The ledger's rows carry share bars by the same measure.
    expect(document.querySelectorAll('tbody [data-share]')).toHaveLength(2)
  })

  test('the measure and the date preset live in the address, through reload and back', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 10, 16, 12))
    try {
      const calls = serve()
      open('#/analytics')
      await figure()
      await act(async () => fireEvent.click(screen.getByRole('radio', { name: 'Cost' })))
      await act(async () => fireEvent.click(screen.getByRole('radio', { name: 'Last 7 days' })))
      const params = () => new URLSearchParams(window.location.hash.split('?')[1])
      expect(params().get('measure')).toBe('cost')
      expect(params().get('range')).toBe('7')
      expect(params().has('from')).toBe(false)
      await waitFor(() => {
        const last = calls.filter((url) => url.pathname === '/api/analytics/usage').at(-1)!
        expect(last.searchParams.get('started_from')).toBe(new Date(2026, 10, 10).toISOString())
        expect(last.searchParams.get('started_before')).toBe(new Date(2026, 10, 17).toISOString())
      })
      expect(screen.getByLabelText('Started on or after')).toHaveValue('2026-11-10')

      // Reload: the same address draws the same choices.
      cleanup()
      open(window.location.hash)
      expect(await screen.findByRole('heading', { name: 'Cost per day, by harness' })).toBeInTheDocument()
      expect(screen.getByRole('radio', { name: 'Cost' })).toBeChecked()
      expect(screen.getByRole('radio', { name: 'Last 7 days' })).toBeChecked()

      // Back undoes the preset, then the measure.
      await act(async () => window.history.back())
      await waitFor(() => expect(screen.getByRole('radio', { name: 'All time' })).toBeChecked())
      expect(screen.getByRole('radio', { name: 'Cost' })).toBeChecked()
      await act(async () => window.history.back())
      await waitFor(() => expect(screen.getByRole('radio', { name: 'Tokens' })).toBeChecked())
    } finally {
      vi.useRealTimers()
    }
  })

  test('an edited date replaces the preset with the days it stood for', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 10, 16, 12))
    try {
      serve()
      // The dates fieldset is shared; History has the same one.
      open('#/analytics?range=30')
      const from = await screen.findByLabelText('Started on or after')
      expect(from).toHaveValue('2026-10-18')
      await act(async () => fireEvent.change(from, { target: { value: '2026-10-20' } }))
      const params = new URLSearchParams(window.location.hash.split('?')[1])
      expect([params.get('range'), params.get('from'), params.get('to')]).toEqual([null, '2026-10-20', '2026-11-16'])
      expect(screen.getByRole('radio', { name: 'Last 30 days' })).not.toBeChecked()
    } finally {
      vi.useRealTimers()
    }
  })

  test('runs over the whole date range, with true zeros where nothing was active', async () => {
    serve()
    open('#/analytics?from=2026-11-01&to=2026-11-05')
    const drawn = await figure()
    // The service's three days, and one more at each end of the range.
    const rows = within(within(drawn).getByRole('table')).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(5)
    expect(rows[0]).toHaveTextContent(/^\S+ \d+00$/)
    expect(rows[4]).toHaveTextContent(/00$/)
  })

  test('says how many sessions reported cost, as the ledger counts them, even by model', async () => {
    serve(
      () => ({ ...REPORT, group: 'model' }),
      () => ({ ...SERIES, group: 'model', series: SERIES.series.map((line) => ({ ...line, points: line.points.map((p) => ({ ...p, sessions: null })) })) }),
    )
    open('#/analytics?group=model&measure=cost')
    await screen.findByText(/in all, the same total/)
    expect(screen.getByRole('heading', { name: 'Cost per day, by model' }).closest('figure')).toHaveTextContent(
      '$0.25 in all, the same total as the table below. Reported by 1 of 3 sessions.',
    )
  })

  test('the ledger lists its rows by the chosen measure', async () => {
    const report: UsageReport = {
      ...REPORT,
      rows: [row({ key: 'pi', sessions: 1, input_tokens: 10 }), row({ key: 'codex', sessions: 5, input_tokens: 1 })],
    }
    serve(() => report)
    open('#/analytics?measure=sessions')
    const table = await screen.findByRole('table', { name: 'Usage by Harness' })
    const names = within(table).getAllByRole('rowheader').map((cell) => cell.textContent)
    expect(names.slice(0, 2)).toEqual(['Codex', 'Pi'])
  })

  test('sessions by model say why they are not drawn', async () => {
    serve()
    open('#/analytics?group=model&measure=sessions')
    expect(await screen.findByText('Sessions are not split by model')).toBeInTheDocument()
  })
})

describe('tool use as a figure', () => {
  test('ranks tools by calls, hatches the failed part with its count, and matches the table', async () => {
    serve()
    open('#/analytics/tools')
    const title = await screen.findByRole('heading', { name: 'Calls by tool' })
    const figure = title.closest('figure') as HTMLElement
    const marks = [...figure.querySelectorAll('[role="img"]')]
    expect(marks.map((mark) => mark.getAttribute('aria-label'))).toEqual([
      'bash: 40, 3 errors of 10 matched',
      'read_file: 7, errors not known, no result matched',
    ])
    expect(marks[0].getAttribute('data-part')).toBe('3')
    expect(figure).toHaveTextContent('40 · 3 errors')
    expect(within(figure).getByRole('list', { name: 'Legend' })).toHaveTextContent('CallsErrors')
    // The table on the page is the figure's twin: same tools, same calls.
    expect(figure.querySelector('details')).toBeNull()
    const table = screen.getByRole('table', { name: 'Tool calls by tool' })
    const rows = within(table).getAllByRole('row').slice(1, -1)
    expect(rows.map((row) => [within(row).getByRole('rowheader').textContent, within(row).getAllByRole('cell')[0].textContent])).toEqual([
      ['bash', '40'],
      ['read_file', '7'],
    ])
  })
})

describe('activity', () => {
  test('is a destination of its own, with its own title', async () => {
    serve()
    open('#/analytics/activity')
    expect(await screen.findByRole('heading', { level: 1, name: 'Activity' })).toBeInTheDocument()
    expect(document.title).toBe('Activity · agdash')
    const nav = screen.getByRole('navigation', { name: 'Analytics sections' })
    expect(within(nav).getByRole('link', { name: 'Activity' })).toHaveAttribute('aria-current', 'page')
  })

  test('shows the hour-of-week grid and sessions at once for the filters, in the reader\'s time zone', async () => {
    const calls = serve()
    open('#/analytics/activity?harness=pi&from=2026-11-02&to=2026-11-02')
    const grid = (await screen.findByRole('heading', { name: 'Activity by hour of the week' })).closest('figure') as HTMLElement
    await waitFor(() => expect(grid.querySelector('[role="group"]')).not.toBeNull())
    const sent = calls.find((url) => url.pathname === '/api/analytics/activity')!
    expect(sent.searchParams.get('time_zone')).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone)
    expect(sent.searchParams.getAll('harness')).toEqual(['pi'])
    expect(sent.searchParams.get('bucket')).toBe('quarter-hour')
    expect(grid).toHaveTextContent(`on your clock (${Intl.DateTimeFormat().resolvedOptions().timeZone})`)
    // The grid's twin: Monday 09:00 is 2, Tuesday 15:00 is 3.
    const twin = within(grid).getByRole('table')
    expect(within(within(twin).getByRole('row', { name: /^Mon/ })).getAllByRole('cell')[9]).toHaveTextContent('2')
    expect(within(within(twin).getByRole('row', { name: /^Tue/ })).getAllByRole('cell')[15]).toHaveTextContent('3')

    const parallel = screen.getByRole('heading', { name: 'Sessions at once, per quarter hour' }).closest('figure') as HTMLElement
    const panels = [...parallel.querySelectorAll('text.text-label')].map((text) => text.textContent)
    expect(panels).toEqual(['All sessions', 'Pi', 'Codex'])
    const columns = within(within(parallel).getByRole('table')).getAllByRole('columnheader').map((cell) => cell.textContent)
    expect(columns.slice(1)).toEqual(['All sessions', 'Pi', 'Codex'])
  })

  test('by the day it says sessions active each day, and one harness is not drawn twice', async () => {
    serve(undefined, undefined, { ...ACTIVITY, bucket: 'day', parallel: [{ key: 'pi', sessions: [2, 1, 1] }] })
    open('#/analytics/activity?harness=pi&range=30')
    const figure = (await screen.findByRole('heading', { name: 'Sessions active each day' })).closest('figure') as HTMLElement
    await waitFor(() => expect(figure.querySelector('[role="group"]')).not.toBeNull())
    expect([...figure.querySelectorAll('text.text-label')].map((text) => text.textContent)).toEqual(['All sessions'])
  })
})
