import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactElement } from 'react'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { newQueryClient } from '../api/queryClient'
import type { ProjectActivity, ProjectBoard, ProjectGroup, SessionDetail, SessionMapping } from '../api/types'
import { TIER_DASH } from '../charts/tiers'
import { createI18n } from '../i18n'
import { MappingFigure } from '../session/MappingFigure'
import { openTwin } from '../test/twin'
import { AgentsFigure } from './ProjectFigures'

// Synthetic projects, agents and sessions only.
const NOW = Date.parse('2026-11-17T12:00:00Z')
const iso = (hoursAgo: number) => new Date(NOW - hoursAgo * 3_600_000).toISOString()

const ACTIVITY: ProjectActivity = {
  connection: { id: 1, name: 'Team', server: 'http://127.0.0.1:8480' },
  days: 7,
  since: iso(7 * 24),
  until: iso(0),
  lanes: [
    {
      project: { id: 1, name: 'Orchard' },
      sessions: [
        { id: 1, harness: 'codex', title: 'Prune the rows', state: 'ended', parent_session_id: null, start: iso(50), end: iso(48) },
        { id: 2, harness: 'codex', title: 'Water the beds', state: 'active', parent_session_id: null, start: iso(3), end: iso(1) },
      ],
    },
    {
      project: { id: 2, name: 'Meadow' },
      sessions: [{ id: 3, harness: 'pi', title: 'Mow the field', state: 'idle', parent_session_id: null, start: iso(26), end: iso(25) }],
    },
    { project: null, sessions: [{ id: 4, harness: 'claude_code', title: 'Loose ends', state: 'ended', parent_session_id: null, start: iso(5), end: iso(4) }] },
  ],
  omitted: 0,
}

const BOARD: ProjectBoard = {
  connection: { id: 1, name: 'Team', server: 'http://127.0.0.1:8480' },
  connections: [{ id: 1, name: 'Team', server: 'http://127.0.0.1:8480' }],
  groups: [],
  flags: [],
  generated_at: iso(0),
} as unknown as ProjectBoard

function show(ui: ReactElement) {
  return render(
    <QueryClientProvider client={newQueryClient()}>
      <I18nextProvider i18n={createI18n()}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('project activity', () => {
  beforeEach(() => {
    window.__AGDASH__ = { token: 'test-token' }
  })

  test("shows each project's sessions over the chosen range, with the now line, and the range lives in the address", async () => {
    const calls: URL[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) => {
        const url = new URL(input, 'http://127.0.0.1:8765')
        calls.push(url)
        if (url.pathname === '/api/projects/activity') return new Response(JSON.stringify({ ...ACTIVITY, days: Number(url.searchParams.get('days')) }))
        if (url.pathname === '/api/projects/board') return new Response(JSON.stringify(BOARD))
        return new Response('{}', { status: 404 })
      }),
    )
    window.location.hash = '#/projects'
    render(<App queryClient={newQueryClient()} />)
    const figure = (await screen.findByRole('heading', { name: 'Sessions by project, last 7 days' })).closest('figure')!
    await waitFor(() => expect(figure.querySelector('[role="group"]')).not.toBeNull())
    // One lane per project, sessions mapped to none last, each session a span on it.
    for (const name of ['Orchard', 'Meadow', 'Unmapped']) expect(within(figure).getAllByText(name).length).toBeGreaterThan(0)
    const marks = [...figure.querySelectorAll('[role="img"]')].map((mark) => mark.getAttribute('aria-label') ?? '')
    expect(marks.filter((label) => label.startsWith('Orchard,'))).toHaveLength(2)
    expect(marks.filter((label) => label.startsWith('Unmapped,'))).toHaveLength(1)
    expect(figure.querySelector('line.stroke-planning-thread')).not.toBeNull()
    expect(within(figure).getByText('Now')).toBeInTheDocument()

    await act(async () => fireEvent.click(screen.getByRole('radio', { name: 'Last 30 days' })))
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('days')).toBe('30')
    await waitFor(() => expect(calls.some((url) => url.pathname === '/api/projects/activity' && url.searchParams.get('days') === '30')).toBe(true))
    expect(await screen.findByRole('heading', { name: 'Sessions by project, last 30 days' })).toBeInTheDocument()
  })
})

describe('declared and observed', () => {
  const group: ProjectGroup = {
    project: { id: 1, name: 'Orchard', status: 'active', health: 'on_track' },
    reason: 'mapped',
    agents: [
      { id: 31, name: 'Robin', role: 'worker', live_sessions: 3, assigned: true, assignments: 2, observed_bases: { run: 2, capabilities: 1 } },
      { id: 32, name: 'Wren', role: 'verifier', live_sessions: 0, assigned: true, assignments: 1, observed_bases: {} },
    ],
    sessions: [],
  }

  test("matches the resolver's counts, and says how each observed session was mapped on focus", () => {
    const { container } = show(<AgentsFigure group={group} />)
    expect([...container.querySelectorAll('[data-declared]')].map((row) => [row.getAttribute('data-declared'), row.getAttribute('data-observed')])).toEqual([
      ['2', '3'],
      ['1', '0'],
    ])
    // Each row says its pair in words, at the row's end.
    expect(container.querySelector('[data-declared="2"]')).toHaveTextContent('2 assigned · 3 live')
    const plot = container.querySelector<HTMLElement>('[role="group"]')!
    act(() => plot.focus())
    const active = document.getElementById(plot.getAttribute('aria-activedescendant')!)!
    expect(active.getAttribute('aria-label')).toBe('Robin: Declared assignments 2, Observed live sessions 3 (Its run: 2, Its own WorkChord call: 1)')
    expect(container.querySelector('.shadow-popover')).toHaveTextContent('Its run: 2, Its own WorkChord call: 1')
    openTwin(container)
    const twin = within(container).getByRole('table')
    expect(within(twin).getByRole('row', { name: /Wren/ })).toHaveTextContent('Wren10–')
  })
})

describe("a session's mapping", () => {
  const detail = { id: 7, title: 'Synthetic session', harness: 'codex' } as unknown as SessionDetail
  const serve = (mapping: SessionMapping) =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(mapping))),
    )
  const connection = (patch: Partial<SessionMapping['connections'][number]>): SessionMapping['connections'][number] => ({
    connection_id: 1,
    server: 'http://127.0.0.1:8480',
    runs: [],
    actor: { state: 'unmapped', target: null, name: null, basis: null, suggestion: null, evidence: [] },
    project: { state: 'unmapped', target: null, name: null, basis: null, suggestion: null, evidence: [] },
    ...patch,
  })

  test('draws session, agent, project and runs, each edge by its evidence, a weak one as a suggestion', async () => {
    serve({
      session_id: 7,
      connections: [
        connection({
          actor: { state: 'mapped', target: 31, name: 'Robin', basis: 'run', suggestion: null, evidence: [] },
          project: {
            state: 'conflict',
            target: null,
            name: null,
            basis: null,
            suggestion: 2,
            evidence: [
              { basis: 'rule', target: 2, name: 'Meadow', evidence: {} },
              { basis: 'rule', target: 1, name: 'Orchard', evidence: {} },
            ],
          },
          runs: [
            { basis: 'tool_call', confidence: 1, suggestion: false, run_id: 501, task_id: 101, task_key: 'ORC-1', task_title: 'Prune the rows', evidence: {} },
            { basis: 'heuristic', confidence: 0.4, suggestion: true, run_id: 502, task_id: 102, task_key: 'ORC-2', task_title: 'Water the beds', evidence: {} },
          ],
        }),
      ],
    })
    const { container } = show(<MappingFigure session={detail} />)
    const figure = (await screen.findByRole('heading', { name: 'WorkChord mapping' })).closest('figure')!
    await waitFor(() => expect(figure.querySelectorAll('[data-tier]').length).toBe(4))
    const edges = [...figure.querySelectorAll('[data-tier]')].map((edge) => ({
      tier: edge.getAttribute('data-tier'),
      dash: edge.querySelector('path')?.getAttribute('stroke-dasharray') ?? undefined,
      label: edge.querySelector('text')?.textContent,
    }))
    expect(edges).toEqual([
      { tier: 'tool_call', dash: TIER_DASH.tool_call, label: 'Its run' },
      { tier: 'heuristic', dash: TIER_DASH.heuristic, label: 'Suggestion' },
      { tier: 'tool_call', dash: TIER_DASH.tool_call, label: 'Tool call' },
      { tier: 'heuristic', dash: TIER_DASH.heuristic, label: 'Suggestion' },
    ])
    expect(within(container).getByRole('list', { name: 'Legend' })).toHaveTextContent('Recorded link')
    expect(within(container).getByRole('list', { name: 'Legend' })).toHaveTextContent('Suggestion')
    expect(figure).toHaveTextContent('Project: Meadow')
  })

  test('names the line form in a legend even when every link is known the same way', async () => {
    serve({
      session_id: 7,
      connections: [connection({ actor: { state: 'mapped', target: 31, name: 'Robin', basis: 'run', suggestion: null, evidence: [] } })],
    })
    const { container } = show(<MappingFigure session={detail} />)
    await screen.findByRole('heading', { name: 'WorkChord mapping' })
    expect(await within(container).findByRole('list', { name: 'Legend' })).toHaveTextContent('Recorded link')
  })

  test('says so when nothing maps the session, and is left out with no server connected', async () => {
    serve({ session_id: 7, connections: [connection({})] })
    const first = show(<MappingFigure session={detail} />)
    expect(await screen.findByText('Not mapped')).toBeInTheDocument()
    expect(screen.getByText(/Nothing links this session to a WorkChord agent/)).toBeInTheDocument()
    first.unmount()
    serve({ session_id: 7, connections: [] })
    const { container } = show(<MappingFigure session={detail} />)
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled())
    await act(async () => {})
    expect(container.querySelector('figure')).toBeNull()
  })
})

