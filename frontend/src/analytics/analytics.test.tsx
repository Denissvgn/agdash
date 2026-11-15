import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import App from '../App'
import { newQueryClient } from '../api/queryClient'
import type { Facets, ToolReport, UsageReport, UsageRow } from '../api/types'

// Synthetic figures only.
const FACETS: Facets = {
  harnesses: [{ value: 'pi', count: 2 }, { value: 'codex', count: 1 }],
  surfaces: [],
  states: [],
  models: [{ value: 'model-a', count: 1 }],
  folders: [],
  auth_methods: [],
  first_started_at: null,
  last_started_at: null,
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

function serve(usage: (url: URL) => UsageReport = () => REPORT) {
  const calls: URL[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      calls.push(url)
      const body =
        url.pathname === '/api/sessions/facets' ? FACETS : url.pathname === '/api/analytics/tools' ? TOOLS : usage(url)
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
