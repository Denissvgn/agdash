import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { newQueryClient } from '../api/queryClient'
import type { MappingRule, Pairing, ProjectOption, UnmappedQueue, WorkChordConnection } from '../api/types'

// Synthetic servers, projects and sessions: nothing here comes from real use.
const SERVER: WorkChordConnection = {
  id: 1,
  name: 'Team',
  server: 'http://127.0.0.1:8480',
  auth_mode: 'managed',
  credential: 'read_only_session',
  agent_key: false,
  publish: false,
  sync_state: 'ok',
  last_synced_at: '2026-11-16T09:00:00Z',
  last_error: null,
  objects: [
    { kind: 'projects', state: 'ok', detail: null, rows: 2, checked_at: '2026-11-16T09:00:00Z', synced_at: null },
    {
      kind: 'runs',
      state: 'unavailable',
      detail: 'runs need an agent key',
      rows: null,
      checked_at: '2026-11-16T09:00:00Z',
      synced_at: null,
    },
  ],
}
const PROJECTS: ProjectOption[] = [
  { id: 1, name: 'Orchard' },
  { id: 2, name: 'Meadow' },
]

type Call = { method: string; path: string; body: unknown }

function serve(state: {
  connections: WorkChordConnection[]
  rules?: MappingRule[]
  queue?: UnmappedQueue
  pairing?: Pairing
  approved?: () => boolean
}) {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method, path: url.pathname, body })
      const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
      const path = url.pathname
      if (method === 'GET' && path === '/api/workchord/connections') return json(state.connections)
      if (method === 'GET' && path.endsWith('/projects')) return json(PROJECTS)
      if (method === 'GET' && path.endsWith('/rules')) return json(state.rules ?? [])
      if (method === 'GET' && path === '/api/mapping/unmapped') return json(state.queue ?? { items: [], total: 0 })
      if (method === 'POST' && path === '/api/workchord/pairings') return json(state.pairing, 202)
      if (method === 'POST' && path.endsWith('/finish')) {
        if (!state.approved?.()) return json({ status: 'waiting' }, 202)
        state.connections = [SERVER]
        return json({ status: 'connected', connection_id: 1 }, 201)
      }
      if (method === 'POST' && path.endsWith('/rules')) {
        const made: MappingRule = {
          id: 7,
          match: 'folder',
          value: body.value,
          session_id: null,
          target: 'project',
          target_value: String(body.project_id),
          target_name: 'Orchard',
          harness: null,
          note: null,
        }
        state.rules = [made]
        return json(made, 201)
      }
      if (method === 'POST' && path.endsWith('/pins')) {
        state.queue = { items: [], total: 0 }
        return json({ id: 8 }, 201)
      }
      return json({ detail: 'not found' }, 404)
    }),
  )
  return calls
}

function open(path: string) {
  window.location.hash = `#${path}`
  render(<App queryClient={newQueryClient()} />)
}

beforeEach(() => {
  window.__AGDASH__ = { token: 'test-token' }
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the WorkChord settings', () => {
  test('a server is paired without leaving the app', async () => {
    let approved = false
    const calls = serve({
      connections: [],
      pairing: {
        status: 'waiting',
        connection_id: null,
        pairing_id: 'p1',
        verification_code: 'ABCD-EF23',
        verification_url: 'http://127.0.0.1:8480/mobile/connect?request=r1',
        expires_at: '2026-11-16T09:10:00Z',
        read_only: true,
      },
      approved: () => approved,
    })
    open('/settings/workchord')
    expect(await screen.findByRole('heading', { name: 'No WorkChord server connected' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Server address'), { target: { value: 'http://127.0.0.1:8480' } })
    fireEvent.change(screen.getByLabelText('Name (optional)'), { target: { value: 'Team' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))

    expect(await screen.findByText('ABCD-EF23')).toBeInTheDocument()
    expect(screen.getByText('agdash asks for a read-only session.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open the approval page/ })).toHaveAttribute(
      'href',
      'http://127.0.0.1:8480/mobile/connect?request=r1',
    )
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ address: 'http://127.0.0.1:8480', name: 'Team' })

    approved = true
    const server = await screen.findByRole('region', { name: 'Team' }, { timeout: 5000 })
    expect(within(server).getByText('Read-only session')).toBeInTheDocument()
    expect(within(server).getByText('Not readable')).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'POST' && c.path === '/api/workchord/pairings/p1/finish')).toBe(true)
  })

  test('a folder rule is added and shown with what it matches', async () => {
    const calls = serve({ connections: [SERVER] })
    open('/settings/workchord')
    const server = await screen.findByRole('region', { name: 'Team' })
    expect(await within(server).findByText('No rules yet.')).toBeInTheDocument()
    fireEvent.change(within(server).getByLabelText('Folder'), { target: { value: '/work/orchard' } })
    await waitFor(() => expect(within(server).getByRole('option', { name: 'Orchard' })).toBeInTheDocument())
    fireEvent.change(within(server).getByLabelText('Project'), { target: { value: '1' } })
    fireEvent.click(within(server).getByRole('button', { name: 'Add rule' }))

    expect(await within(server).findByText('Folder /work/orchard')).toBeInTheDocument()
    expect(within(server).getByText('to project Orchard')).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'POST')).toEqual({
      method: 'POST',
      path: '/api/workchord/connections/1/rules',
      body: { match: 'folder', value: '/work/orchard', project_id: 1 },
    })
    expect(within(server).getByRole('button', { name: 'Remove the rule for Folder /work/orchard' })).toBeInTheDocument()
  })

  test('the service’s reason is shown when a rule is refused', async () => {
    serve({ connections: [SERVER] })
    open('/settings/workchord')
    const server = await screen.findByRole('region', { name: 'Team' })
    await waitFor(() => expect(within(server).getByRole('option', { name: 'Orchard' })).toBeInTheDocument())
    const original = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      init?.method === 'POST'
        ? new Response(JSON.stringify({ detail: 'give the folder as an absolute path' }), { status: 422 })
        : original(input, init),
    )
    fireEvent.change(within(server).getByLabelText('Folder'), { target: { value: 'work' } })
    fireEvent.change(within(server).getByLabelText('Project'), { target: { value: '2' } })
    fireEvent.click(within(server).getByRole('button', { name: 'Add rule' }))
    expect(await within(server).findByText('Not added: give the folder as an absolute path')).toBeInTheDocument()
  })
})

describe('the Unmapped queue', () => {
  const queue: UnmappedQueue = {
    total: 2,
    items: [
      {
        session_id: 11,
        harness: 'codex',
        title: 'Synthetic session 11',
        cwd: '/srv/x',
        state: 'ended',
        last_activity_at: '2026-11-16T08:00:00Z',
        connection_id: 1,
        reason: 'no evidence',
        suggestion: null,
        evidence: [],
      },
      {
        session_id: 12,
        harness: 'pi',
        title: 'Synthetic session 12',
        cwd: '/work/orchard',
        state: 'ended',
        last_activity_at: '2026-11-16T07:00:00Z',
        connection_id: 1,
        reason: 'conflicting evidence',
        suggestion: 1,
        evidence: [
          { basis: 'task', target: 1, name: 'Orchard', evidence: {} },
          { basis: 'rule', target: 2, name: 'Meadow', evidence: {} },
        ],
      },
    ],
  }

  test('every piece of evidence names its basis', async () => {
    serve({ connections: [SERVER], queue })
    open('/projects/unmapped')
    const list = await screen.findByRole('list', { name: 'Unmapped sessions' })
    expect(within(list).getByText('No evidence points at a project.')).toBeInTheDocument()
    const evidence = within(list).getByRole('list', { name: 'Evidence for Synthetic session 12' })
    expect(within(evidence).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Its task: Orchard',
      'Folder or repository rule: Meadow',
    ])
    expect(screen.getByText('2 sessions without a project')).toBeInTheDocument()
  })

  test('a session is pinned from the queue', async () => {
    const calls = serve({ connections: [SERVER], queue })
    open('/projects/unmapped')
    const list = await screen.findByRole('list', { name: 'Unmapped sessions' })
    const first = within(list).getAllByRole('listitem')[0]
    const select = within(first).getByLabelText('Pin to project')
    await waitFor(() => expect(within(first).getByRole('option', { name: 'Meadow' })).toBeInTheDocument())
    fireEvent.change(select, { target: { value: '2' } })
    await act(async () => fireEvent.click(within(first).getByRole('button', { name: 'Pin' })))
    expect(calls.find((c) => c.method === 'POST')).toEqual({
      method: 'POST',
      path: '/api/workchord/connections/1/pins',
      body: { session_id: 11, project_id: 2 },
    })
    expect(await screen.findByRole('heading', { name: 'Every session is mapped' })).toBeInTheDocument()
  })

  test('without a server it says where to connect one', async () => {
    serve({ connections: [] })
    open('/projects/unmapped')
    expect(await screen.findByRole('heading', { name: 'No WorkChord server connected' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open WorkChord settings' })).toHaveAttribute('href', '#/settings/workchord')
  })
})
