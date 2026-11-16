import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { newQueryClient } from '../api/queryClient'
import type { EventOut, SessionDetail, SessionOverview, StateHistory } from '../api/types'
import { formatMoment } from '../charts/format'
import { createI18n } from '../i18n'
import { SessionOverviewFigure } from './Overview'
import { overviewModel } from './overviewModel'

// A synthetic session over two mornings: 09:00 to 12:00 on 16 and 17 November, with
// the night between empty, so it is compressed into a break.
const HOUR = 3_600_000
const START = Date.parse('2026-11-16T09:00:00Z')
const iso = (ms: number) => new Date(ms).toISOString()
const busyHour = (h: number) => h % 24 < 3
// Times as the page shows them, on the reader's clock.
const shown = (ms: number) => formatMoment(new Date(ms))

function synthetic(hours = 27, turnsReported = (i: number) => i % 3 !== 1): SessionOverview {
  const buckets = Array.from({ length: hours }, (_, h) => ({ start: iso(START + h * HOUR), end: iso(START + (h + 1) * HOUR) }))
  return {
    session_id: 1,
    time_zone: 'UTC',
    bucket: 'hour',
    buckets,
    activity: buckets.map((_, h) =>
      busyHour(h)
        ? {
            prompts: 2,
            replies: 5 + h,
            thoughts: { full: 3, summary: 1, encrypted: 2, absent: 0, not_recorded: 1 },
            tool_calls: 8,
            tool_errors: h % 2,
          }
        : { prompts: 0, replies: 0, thoughts: { full: 0, summary: 0, encrypted: 0, absent: 0, not_recorded: 0 }, tool_calls: 0, tool_errors: 0 },
    ),
    undated_events: 0,
    markers: [
      { kind: 'model_change', sequence: 10, at: iso(START + 0.5 * HOUR), model: 'model-b', previous_model: 'model-a', effort: null, trigger: null, tokens_before: null, tokens_after: null },
      { kind: 'compaction', sequence: 90, at: iso(START + 25.5 * HOUR), model: null, previous_model: null, effort: null, trigger: 'auto', tokens_before: 90_000, tokens_after: 20_000 },
    ],
    markers_omitted: 0,
    subagents: [
      { session_id: 2, harness: 'claude_code', parent_basis: 'harness_record', title: 'Explore', state: 'ended', start: iso(START + HOUR), end: iso(START + 2 * HOUR), ended: true },
    ],
    subagents_omitted: 0,
    turns: Array.from({ length: 6 }, (_, i) => {
      const at = START + (i < 3 ? i : 24 + (i - 3)) * HOUR
      const reported = turnsReported(i)
      return {
        index: i + 1,
        prompt_sequence: i * 10,
        started_at: iso(at),
        messages: reported ? 3 : 0,
        input_tokens: reported ? 1000 * (i + 1) : null,
        output_tokens: reported ? 100 : null,
        cache_read_tokens: null,
        cache_write_tokens: null,
        reasoning_tokens: null,
        reported_cost: null,
        reported_by: {},
      }
    }),
  }
}

const STATES: StateHistory = {
  session_id: 1,
  state: 'ended',
  changes: [
    { state: 'active', basis: 'harness_busy', previous: null, changed_at: iso(START) },
    { state: 'waiting', basis: 'harness_waiting', previous: 'active', changed_at: iso(START + 2 * HOUR) },
    { state: 'active', basis: 'harness_busy', previous: 'waiting', changed_at: iso(START + 24 * HOUR) },
    { state: 'ended', basis: 'process_exited', previous: 'active', changed_at: iso(START + 27 * HOUR) },
  ],
  earlier_omitted: 0,
}

const gaps0 = (container: HTMLElement) => container.querySelector('[data-gap]')!

function draw(overview = synthetic(), view: [number, number] | null = null, onSeek = vi.fn()) {
  const model = overviewModel(overview, STATES, START + 30 * HOUR)
  const result = render(
    <I18nextProvider i18n={createI18n()}>
      <SessionOverviewFigure model={model} view={view} onSeek={onSeek} />
    </I18nextProvider>,
  )
  return { ...result, model, onSeek }
}

describe('the session overview', () => {
  test('draws every lane on one time axis, with the night compressed and marked', () => {
    const { container } = draw()
    const lane = (name: string) => container.querySelector(`[data-lane="${name}"]`)!
    // Counts: prompts, replies, thoughts and tool calls as columns, the parts textured.
    expect(lane('counts').querySelectorAll('path').length).toBeGreaterThan(20)
    expect(lane('counts').querySelectorAll('path[fill^="url(#"]').length).toBeGreaterThan(0)
    expect(lane('subagents').querySelectorAll('rect')).toHaveLength(1)
    expect(lane('markers').querySelectorAll('circle')).toHaveLength(1)
    expect(lane('markers').querySelectorAll('rect')).toHaveLength(1)
    expect([...lane('state').querySelectorAll('[data-state]')].map((r) => r.getAttribute('data-state'))).toEqual([
      'active',
      'waiting',
      'active',
      'ended',
    ])
    expect(lane('tokens').querySelectorAll('[data-turn]').length).toBe(6)
    // No turn's column crosses the break: the last turn of the first morning stops at it.
    const breakX = Number(gaps0(container).querySelector('rect')!.getAttribute('x'))
    for (const turn of lane('tokens').querySelectorAll('[data-turn] path, [data-turn="unreported"] rect')) {
      const left = Number(turn.getAttribute('x') ?? Number.NaN)
      if (!Number.isNaN(left) && left < breakX) expect(left + Number(turn.getAttribute('width'))).toBeLessThanOrEqual(breakX + 1)
    }
    // The empty night is one compressed break, from the end of the first morning to the second.
    const gaps = [...container.querySelectorAll('[data-gap]')]
    expect(gaps).toHaveLength(1)
    expect(gaps[0].getAttribute('data-gap')).toBe(`${START + 3 * HOUR}-${START + 24 * HOUR}`)
    expect(gaps[0].querySelectorAll('line')).toHaveLength(2)
    // Every lane is named.
    for (const name of ['Prompts', 'Replies', 'Thoughts', 'Tool calls', 'Subagents', 'Model changes', 'Compactions', 'State', 'Tokens per turn']) {
      expect(within(container).getAllByText(name).length).toBeGreaterThan(0)
    }
  })

  test('hatches a turn that reported no usage, and says so in the table, never as zero', () => {
    const { container } = draw()
    const tokens = container.querySelector('[data-lane="tokens"]')!
    expect(tokens.querySelectorAll('[data-turn="unreported"]')).toHaveLength(2)
    expect(tokens.querySelectorAll('[data-turn="unreported"] rect[fill^="url(#"]')).toHaveLength(2)
    expect(tokens.querySelector('[data-turn="0"]')).toBeNull()
    const twin = within(container).getByRole('table')
    const row = within(twin).getAllByRole('row').find((r) => r.textContent?.startsWith(shown(START + HOUR)))!
    expect(within(row).getAllByRole('cell')[9]).toHaveTextContent('not reported')
  })

  test('marks the part in view, and the window seeks by keyboard and pointer', () => {
    const onSeek = vi.fn()
    const { container } = draw(synthetic(), [START + HOUR, START + 2 * HOUR], onSeek)
    const window = screen.getByRole('slider', { name: 'Part of the timeline in view' })
    expect(window).toHaveAttribute('aria-valuetext', `${shown(START + HOUR)} to ${shown(START + 2 * HOUR)}`)
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(onSeek).toHaveBeenLastCalledWith(START + 2 * HOUR)
    fireEvent.keyDown(window, { key: 'End' })
    expect(onSeek).toHaveBeenLastCalledWith(START + 26 * HOUR)
    // A press on the drawing moves the list to the moment under the pointer.
    const box = container.querySelector<HTMLElement>('[data-overview]')!.getBoundingClientRect()
    const drawing = container.querySelector<HTMLElement>('[data-overview] > div')!
    fireEvent.pointerUp(drawing, { clientX: box.left + 130, button: 0 })
    expect(onSeek).toHaveBeenCalledTimes(3)
    expect(onSeek.mock.lastCall![0]).toBeGreaterThanOrEqual(START)
  })

  test('draws a session of 10,000 events within 200 ms', () => {
    // 120 buckets holding 10,000 events, 1,000 turns, 200 markers and 100 subagents.
    const big = synthetic(120)
    big.activity = big.activity.map((_, h) => ({
      prompts: 8,
      replies: 31,
      thoughts: { full: 20, summary: 0, encrypted: 5, absent: 0, not_recorded: 0 },
      tool_calls: 20,
      tool_errors: h % 3,
    }))
    const total = big.activity.reduce((sum, a) => sum + a.prompts + a.replies + a.thoughts.full + a.thoughts.encrypted + a.tool_calls, 0)
    expect(total).toBeGreaterThanOrEqual(10_000)
    big.turns = Array.from({ length: 1000 }, (_, i) => ({ ...big.turns[0], index: i + 1, started_at: iso(START + i * 7 * 60_000) }))
    big.markers = Array.from({ length: 200 }, (_, i) => ({ ...big.markers[i % 2], at: iso(START + i * 30 * 60_000) }))
    big.subagents = Array.from({ length: 100 }, (_, i) => ({ ...big.subagents[0], session_id: 100 + i, start: iso(START + i * HOUR), end: iso(START + i * HOUR + 1800_000) }))
    // The better of two draws, so a busy runner's one slow moment, or the first draw's
    // warm-up, does not stand for how long drawing takes.
    const times: number[] = []
    for (let i = 0; i < 2; i += 1) {
      const began = performance.now()
      const drawn = draw(big)
      times.push(performance.now() - began)
      if (i === 0) drawn.unmount()
    }
    const took = Math.min(...times)
    // Timed in a real browser (the WebKit run, as on the CI runner); jsdom is far slower at layout-free DOM work.
    if (!navigator.userAgent.includes('jsdom')) expect(took).toBeLessThan(200)
    expect(document.querySelectorAll('[data-lane="tokens"] [data-turn]').length).toBe(1000)
  })
})

// -- on the page, with the list ---------------------------------------------------------

const detail: SessionDetail = {
  id: 1,
  harness: 'claude_code',
  native_id: 'native-1',
  surface: 'cli',
  title: 'Synthetic long session',
  cwd: '/work/example',
  branch: null,
  state: 'ended',
  started_at: iso(START),
  last_activity_at: iso(START + 27 * HOUR),
  ended_at: null,
  models: ['model-a'],
  effort: null,
  parent_session_id: null,
  initial_task: 'Synthetic task',
  event_count: 600,
  child_count: 0,
  permission_mode: null,
  parent_basis: null,
  children: [],
  events_by_kind: {},
}

// 600 events, 100 per busy hour across the six busy hours.
const busyHours = [0, 1, 2, 24, 25, 26]
const EVENTS: EventOut[] = Array.from({ length: 600 }, (_, i) => ({
  id: i + 1,
  session_id: 1,
  sequence: i,
  occurred_at: iso(START + busyHours[Math.floor(i / 100)] * HOUR + (i % 100) * 30_000),
  kind: 'assistant_text',
  payload: { text: `Synthetic reply ${i}` },
  thoughts_coverage: null,
}))

function serve() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      const json = (value: unknown) => new Response(JSON.stringify(value))
      if (url.pathname === '/api/sessions/1') return json(detail)
      if (url.pathname === '/api/sessions/1/overview') return json(synthetic())
      if (url.pathname === '/api/sessions/1/states') return json(STATES)
      if (url.pathname === '/api/sessions/1/events') {
        const from = url.searchParams.get('cursor') ? Number(url.searchParams.get('cursor')) + 1 : 0
        const items = EVENTS.slice(from, from + Number(url.searchParams.get('limit')))
        return json({ items, next_cursor: from + items.length < EVENTS.length ? String(items.at(-1)!.sequence) : null })
      }
      return json({ items: [], next_cursor: null })
    }),
  )
}

describe('the overview over the timeline', () => {
  beforeEach(() => {
    window.__AGDASH__ = { token: 'test-token' }
  })
  afterEach(() => vi.unstubAllGlobals())

  test('its window follows the list as it scrolls, and moving the window scrolls the list', async () => {
    serve()
    window.location.hash = '#/history/sessions/1'
    render(<App queryClient={newQueryClient()} />)
    const region = await screen.findByRole('region', { name: 'Session timeline' })
    const window_ = await screen.findByRole('slider', { name: 'Part of the timeline in view' })
    const first = `${shown(START)} to`
    await waitFor(() => expect(window_.getAttribute('aria-valuetext')).toBeTruthy())
    await waitFor(() => expect(window_.getAttribute('aria-valuetext')?.startsWith(first)).toBe(true))

    // Scrolling the list moves the window.
    await act(async () => {
      region.scrollTop = Number.parseFloat(within(region).getByRole('list').style.height) / 2
      fireEvent.scroll(region)
    })
    await waitFor(() => expect(window_.getAttribute('aria-valuetext')?.startsWith(first)).toBe(false))

    // Moving the window to the second morning scrolls the list to its first event.
    await act(async () => fireEvent.keyDown(window_, { key: 'End' }))
    // The events there are on the second page: it loads, then the list scrolls to them.
    await waitFor(() => expect(region.scrollTop).toBeGreaterThan(Number.parseFloat(within(region).getByRole('list').style.height) * 0.6))
    // A real browser reports the scroll; jsdom needs the event sent.
    await act(async () => fireEvent.scroll(region))
    // The window now starts on the second morning's last hour; rows measured as they draw may
    // settle the list a row or two either way, so the start is checked to within minutes.
    await waitFor(() => expect(Math.abs(Number(window_.getAttribute('aria-valuenow')) - (START + 26 * HOUR))).toBeLessThan(5 * 60_000))
    expect(region.scrollTop).toBeGreaterThan(0)
  })
})
