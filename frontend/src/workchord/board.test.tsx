import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { newQueryClient } from '../api/queryClient'
import type { BoardCard, ProjectBoard } from '../api/types'
import { unnamedControls } from '../test/controls'

// Synthetic projects, sessions and runs only.
const card = (id: number, patch: Partial<BoardCard> = {}): BoardCard => ({
  id,
  harness: 'codex',
  title: `Synthetic session ${id}`,
  state: 'active',
  cwd: `/work/${id}`,
  last_activity_at: '2026-11-16T09:00:00Z',
  parent_session_id: null,
  project_basis: 'task',
  actor: { id: 31, name: 'Robin', role: 'worker', basis: 'run' },
  task: null,
  run: null,
  work: null,
  observed_models: ['model-a'],
  observed_usage: { messages: 2, input_tokens: 700, output_tokens: 70 },
  flags: [],
  subagents: [],
  ...patch,
})

const BOARD: ProjectBoard = {
  connection: { id: 1, name: 'Team', server: 'http://127.0.0.1:8480' },
  connections: [{ id: 1, name: 'Team', server: 'http://127.0.0.1:8480' }],
  generated_at: '2026-11-16T09:00:00Z',
  flags: [
    {
      code: 'run_without_session',
      session_id: null,
      run_id: 502,
      task_id: 102,
      task_key: 'ORC-2',
      actor: 'Wren',
      observed: null,
      bound: null,
      since: null,
    },
    {
      code: 'model_mismatch',
      session_id: 1,
      run_id: 501,
      task_id: null,
      task_key: null,
      actor: null,
      observed: 'model-b',
      bound: 'model-a-0101',
      since: null,
    },
  ],
  groups: [
    {
      project: { id: 1, name: 'Orchard', status: 'active', health: 'on_track' },
      reason: 'mapped',
      agents: [
        { id: 31, name: 'Robin', role: 'worker', live_sessions: 1, assigned: true },
        { id: 32, name: 'Wren', role: 'verifier', live_sessions: 0, assigned: true },
      ],
      sessions: [
        card(1, {
          flags: ['model_mismatch'],
          observed_models: ['model-b'],
          task: {
            id: 101,
            key: 'ORC-1',
            title: 'Prune the rows',
            status: 'in_progress',
            iteration: 'Sprint 7',
            claim_expires_at: '2026-11-16T10:00:00Z',
          },
          run: {
            id: 501,
            status: 'running',
            bound_model: 'model-a-0101',
            declared_usage: { quantities: { input_tokens: 900, output_tokens: 90 } },
            heartbeat_at: null,
          },
          work: 'execution',
          subagents: [card(2, {
              title: 'Synthetic helper',
              parent_session_id: 1,
              actor: null,
              project_basis: null,
              observed_usage: { messages: 1, input_tokens: 5, output_tokens: 1 },
            })],
        }),
      ],
    },
    {
      project: null,
      reason: 'unmapped',
      agents: [],
      sessions: [card(3, { actor: null, project_basis: null, observed_models: [], observed_usage: { messages: 0, input_tokens: null, output_tokens: null } })],
    },
  ],
}

function serve(board: ProjectBoard) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      if (url.pathname === '/api/projects/board') return new Response(JSON.stringify(board))
      return new Response(JSON.stringify({ detail: 'not found' }), { status: 404 })
    }),
  )
}

beforeEach(() => {
  window.__AGDASH__ = { token: 'test-token' }
})

afterEach(() => vi.unstubAllGlobals())

describe('the board by project', () => {
  test('sessions sit under their project and the unmapped ones have their own group', async () => {
    serve(BOARD)
    window.location.hash = '#/projects'
    render(<App queryClient={newQueryClient()} />)
    const orchard = await screen.findByRole('list', { name: 'Live sessions in Orchard' })
    expect(within(orchard).getByRole('link', { name: 'Synthetic session 1' })).toHaveAttribute('href', '#/history/sessions/1')
    expect(within(orchard).getByRole('list', { name: 'Subagents of Synthetic session 1' })).toHaveTextContent('Synthetic helper')
    const unmapped = screen.getByRole('list', { name: 'Live sessions in Unmapped' })
    expect(within(unmapped).getByText('Synthetic session 3')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Map them in the Unmapped queue' })).toHaveAttribute('href', '#/projects/unmapped')

    // Each agent's declared assignments against its observed live sessions.
    const agents = screen.getByRole('heading', { name: 'Declared and observed in Orchard' }).closest('figure')!
    expect([...agents.querySelectorAll('[data-declared]')].map((row) => [row.getAttribute('data-declared'), row.getAttribute('data-observed')])).toEqual([
      ['1', '1'],
      ['1', '0'],
    ])
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('each session shows its task, run, models and usage, with the basis of its mapping', async () => {
    serve(BOARD)
    window.location.hash = '#/projects'
    render(<App queryClient={newQueryClient()} />)
    const orchard = await screen.findByRole('list', { name: 'Live sessions in Orchard' })
    const lead = within(orchard).getAllByRole('listitem')[0]
    expect(within(lead).getByText('ORC-1 Prune the rows')).toBeInTheDocument()
    expect(within(lead).getByText('Status in_progress')).toBeInTheDocument()
    expect(within(lead).getByText('Sprint 7')).toBeInTheDocument()
    expect(within(lead).getByText('Execution')).toBeInTheDocument()
    expect(within(lead).getByText('Run 501: running')).toBeInTheDocument()
    expect(within(lead).getByText('Model bound model-a-0101, observed model-b')).toBeInTheDocument()
    expect(within(lead).getByText('900 in · 90 out tokens')).toBeInTheDocument()
    expect(within(lead).getByText('700 in · 70 out tokens')).toBeInTheDocument()
    expect(within(lead).getByText('Agent Robin (Its run)')).toBeInTheDocument()
    expect(within(lead).getByText('Project from Its task')).toBeInTheDocument()
    expect(within(lead).getByText('Different model')).toBeInTheDocument()
  })

  test('attention flags are listed in words', async () => {
    serve(BOARD)
    window.location.hash = '#/projects'
    render(<App queryClient={newQueryClient()} />)
    const panel = await screen.findByRole('region', { name: '2 things need a look' })
    const items = within(panel).getAllByRole('listitem').map((item) => item.textContent)
    expect(items[0]).toBe(
      'No live session: WorkChord marks run 502 on ORC-2 by Wren as running, and no live session here is linked to it.',
    )
    expect(items[1]).toBe('Different model: Observed model-b, but WorkChord bound model-a-0101. Synthetic session 1')
    expect(within(panel).getByRole('link', { name: 'Synthetic session 1' })).toHaveAttribute('href', '#/history/sessions/1')
  })

  test('signals that say the same of several sessions are one line, with a link a session', async () => {
    const noTask = (session_id: number) => ({ ...BOARD.flags[1], code: 'no_task' as const, session_id, run_id: null })
    serve({ ...BOARD, flags: [BOARD.flags[0], noTask(1), noTask(2), noTask(1)] })
    window.location.hash = '#/projects'
    render(<App queryClient={newQueryClient()} />)
    // Still counted one by one in the heading.
    const panel = await screen.findByRole('region', { name: '4 things need a look' })
    const items = within(panel).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[1]).toHaveTextContent(/^No task: 2 sessions work in a mapped project but are linked to no task: /)
    expect(within(items[1]).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual([
      '#/history/sessions/1',
      '#/history/sessions/2',
    ])
  })

  test('a task with a run going is listed with the sessions seen for it, also when none is live', async () => {
    const task = (id: number, key: string) => ({ id, key, title: `Synthetic task ${key}`, status: 'active', iteration: null, claim_expires_at: null })
    const run = (id: number) => ({ id, status: 'running', bound_model: null, declared_usage: null, heartbeat_at: '2026-11-16T09:55:00Z' })
    const session = { harness: 'claude_code', title: null, models: ['model-a'], started_at: null, ended_at: null, last_activity_at: null }
    serve({
      ...BOARD,
      flags: [],
      groups: [
        {
          project: { id: 6, name: 'Pilot', status: 'active', health: null },
          reason: 'mapped',
          agents: [],
          sessions: [],
          tasks: [
            {
              task: task(701, 'PIL-1'),
              stage: 'executing',
              actor: { id: 43, name: 'Synthetic worker', role: 'worker' },
              run: run(901),
              started_at: '2026-11-16T09:30:00Z',
              declared_harness: 'claude-code',
              sessions: [
                { ...session, id: 11, state: 'ended', basis: 'trace_id', suggested: false },
                { ...session, id: 12, state: 'active', basis: 'heuristic', suggested: true },
              ],
              agent_running: false,
            },
            {
              task: task(702, 'PIL-2'),
              stage: 'executing',
              actor: null,
              run: run(902),
              started_at: null,
              declared_harness: 'codex',
              sessions: [],
              agent_running: false,
            },
          ],
        },
      ],
    })
    window.location.hash = '#/projects'
    render(<App queryClient={newQueryClient()} />)
    const tasks = await screen.findByRole('list', { name: 'Tasks with a run going in Pilot' })
    expect(screen.getByRole('heading', { name: '2 tasks with a run going' })).toBeInTheDocument()
    expect(within(tasks).getByText('PIL-1 Synthetic task PIL-1')).toBeInTheDocument()
    expect(within(tasks).getByText('Synthetic worker')).toBeInTheDocument()
    expect(within(tasks).getAllByText('Stage executing')).toHaveLength(2)
    const seen = within(tasks).getByRole('list', { name: 'Sessions for PIL-1 Synthetic task PIL-1' })
    expect(within(seen).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual([
      '#/history/sessions/11',
      '#/history/sessions/12',
    ])
    expect(within(seen).getByText('Matching record')).toBeInTheDocument()
    expect(within(seen).getByText('Suggestion: it started with the run')).toBeInTheDocument()
    // The only session on evidence is over, and the other task has none at all.
    expect(within(tasks).getByText('No linked agent session is running now.')).toBeInTheDocument()
    expect(within(tasks).getByText('The run declares a Codex session that agdash has not seen on this machine.')).toBeInTheDocument()
    // A project with tasks and no live session has no empty list of sessions.
    expect(screen.queryByRole('list', { name: 'Live sessions in Pilot' })).not.toBeInTheDocument()
  })

  test('without a server or live sessions it says so', async () => {
    serve({ ...BOARD, connection: null, connections: [], flags: [], groups: [] })
    window.location.hash = '#/projects'
    render(<App queryClient={newQueryClient()} />)
    expect(await screen.findByRole('heading', { name: 'No WorkChord server connected' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Nothing is running' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /need a look/ })).not.toBeInTheDocument()
  })
})
