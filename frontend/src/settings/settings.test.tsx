import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { newQueryClient } from '../api/queryClient'
import type { ArchiveSize, Source, SourcesReport, StorageReport } from '../api/types'
import { unnamedControls } from '../test/controls'
import { sizeParts } from './format'

// Synthetic folders, versions and counts: nothing here comes from a real machine.
const source = (patch: Partial<Source> & Pick<Source, 'harness'>): Source => ({
  found: true,
  locations: [{ path: `/home/example/.${patch.harness}`, role: 'records', found: true }],
  files: 0,
  bytes_read: 0,
  last_read_at: null,
  sessions: 0,
  format_version: null,
  formats: [],
  ...patch,
})

const SOURCES: SourcesReport = {
  sources: [
    source({
      harness: 'claude_code',
      locations: [
        { path: '/home/example/.claude', role: 'records', found: true },
        { path: '/home/example/.claude.json', role: 'settings', found: true },
      ],
      files: 1284,
      bytes_read: 412_300_000,
      last_read_at: '2026-11-16T09:30:00Z',
      sessions: 3912,
      format_version: '2.1.14',
      formats: [
        {
          version: '2.1.14',
          unknown_records: 14,
          unreadable_lines: 2,
          first_seen_at: '2026-11-12T08:00:00Z',
          last_seen_at: '2026-11-16T09:30:00Z',
          recent: true,
        },
        {
          version: null,
          unknown_records: 3,
          unreadable_lines: 0,
          first_seen_at: '2026-08-02T08:00:00Z',
          last_seen_at: '2026-08-09T08:00:00Z',
          recent: false,
        },
        {
          version: '2.0.3',
          unknown_records: 0,
          unreadable_lines: 0,
          first_seen_at: '2026-07-01T08:00:00Z',
          last_seen_at: '2026-08-01T08:00:00Z',
          recent: false,
        },
      ],
    }),
    source({
      harness: 'codex',
      files: 1,
      bytes_read: 950,
      last_read_at: '2026-11-15T10:00:00Z',
      sessions: 1,
      format_version: '0.9',
      formats: [
        {
          version: '0.9',
          unknown_records: 0,
          unreadable_lines: 0,
          first_seen_at: '2026-11-01T08:00:00Z',
          last_seen_at: '2026-11-15T10:00:00Z',
          recent: false,
        },
      ],
    }),
    source({
      harness: 'antigravity',
      locations: [
        { path: '/home/example/.gemini/antigravity', role: 'records', found: true },
        { path: '/home/example/.gemini/antigravity-cli', role: 'records', found: false },
        { path: '/home/example/.gemini/google_accounts.json', role: 'settings', found: false },
      ],
    }),
    source({
      harness: 'pi',
      found: false,
      locations: [{ path: '/home/example/.pi/agent', role: 'records', found: false }],
    }),
  ],
}

const STORAGE: StorageReport = {
  store: { file: '/home/example/agdash/agdash.db', bytes: 7_707_283_456, sessions: 7737, first_started_at: '2025-03-03T08:00:00Z' },
  archive: { enabled: true, chunks: 10_841 },
  retention: { days: null, cutoff: null, removable_sessions: null, kept_alive: null },
}

type Served = {
  sources?: SourcesReport | number
  storage?: StorageReport | number
  archive?: ArchiveSize | number | 'never'
  exports?: Record<string, string | number>
}

function serve(served: Served) {
  const asked: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      asked.push(`${url.pathname}${url.search}`)
      const json = (value: unknown) =>
        typeof value === 'number' ? new Response('{}', { status: value }) : new Response(JSON.stringify(value), { status: 200 })
      if (url.pathname === '/api/sources' && served.sources !== undefined) return json(served.sources)
      if (url.pathname === '/api/storage' && served.storage !== undefined) return json(served.storage)
      if (url.pathname === '/api/storage/archive' && served.archive !== undefined) {
        if (served.archive === 'never') return new Promise<Response>(() => {})
        return json(served.archive)
      }
      if (url.pathname === '/api/sessions/1/export') {
        const body = served.exports?.[url.searchParams.get('format') ?? '']
        if (typeof body === 'number') return new Response('{}', { status: body })
        return new Response(body ?? '', { status: 200 })
      }
      if (url.pathname === '/api/sessions/1') {
        return json({
          id: 1,
          harness: 'pi',
          native_id: 'native-1',
          surface: 'cli',
          title: 'Synthetic session',
          cwd: '/work/example',
          branch: null,
          state: 'ended',
          started_at: '2026-11-16T09:00:00Z',
          last_activity_at: '2026-11-16T12:00:00Z',
          ended_at: null,
          models: ['model-a'],
          effort: null,
          parent_session_id: null,
          initial_task: null,
          event_count: 0,
          child_count: 0,
          permission_mode: null,
          parent_basis: null,
          children: [],
          events_by_kind: {},
        })
      }
      if (url.pathname === '/api/sessions/1/events') return json({ items: [], next_cursor: null })
      if (url.pathname === '/api/reading') return json({ known: false, done: 0, total: 0, finished: false })
      return json(404)
    }),
  )
  return asked
}

function open(path: string) {
  window.location.hash = `#${path}`
  render(<App queryClient={newQueryClient()} />)
}

beforeEach(() => {
  window.__AGDASH__ = { token: 'test-token' }
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Settings · Sources', () => {
  test('each harness says whether it was found, where, and what was read', async () => {
    serve({ sources: SOURCES })
    open('/settings')
    const list = await screen.findByRole('list', { name: 'Harnesses' })
    // The count is of what the warning lines say, a format that changed lately, not of every old gap.
    expect(screen.getByText('3 of 4 harnesses found on this machine. 1 may have changed its format.')).toBeInTheDocument()
    const rows = within(list)
      .getAllByRole('heading', { level: 2 })
      .map((heading) => heading.closest('li') as HTMLElement)
    expect(rows.map((row) => within(row).getByRole('heading').textContent)).toEqual(['Claude Code', 'Codex', 'Antigravity', 'Pi'])

    const claude = within(rows[0])
    expect(claude.getByText('Found')).toBeInTheDocument()
    for (const fact of ['1,284 files read', '412.3 MB', '3,912 sessions']) {
      expect(claude.getByText(fact)).toBeInTheDocument()
    }
    expect(claude.getByText('1,284 files read').closest('p')).toHaveTextContent('Format 2.1.14')
    expect(claude.queryByText('Every record understood')).toBeNull()
    const places = claude.getByRole('list', { name: 'Where agdash looks for Claude Code' })
    expect(within(places).getAllByRole('listitem').map((place) => place.textContent)).toEqual([
      '/home/example/.claude',
      '/home/example/.claude.jsonsign-in method only',
    ])

    expect(within(rows[1]).getByText('1 file read')).toBeInTheDocument()
    expect(within(rows[1]).getByText('950 bytes')).toBeInTheDocument()
    expect(within(rows[1]).getByText('1 session')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Every record understood')).toBeInTheDocument()

    // Found, with nothing in it yet: no counts are invented, and each missing place says so.
    const antigravity = within(rows[2])
    expect(antigravity.getByText('No session files read yet')).toBeInTheDocument()
    expect(antigravity.queryByText(/Last read|Nothing read yet/)).toBeNull()
    expect(antigravity.getByText('not found')).toBeInTheDocument()
    expect(antigravity.getByText('not found (sign-in method only)')).toBeInTheDocument()

    const pi = within(rows[3])
    expect(pi.getByText('Not found')).toBeInTheDocument()
    expect(pi.getByText('Nothing of it on this machine')).toBeInTheDocument()
    // The harnesses' own folder settings are named as the settings they are.
    expect(screen.getByText('CLAUDE_CONFIG_DIR').closest('li')).toHaveTextContent(
      /runs with the setting that moved it: CLAUDE_CONFIG_DIR or CODEX_HOME$/,
    )
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('a harness whose folder is gone says what is kept of it, not that it has sessions here', async () => {
    serve({
      sources: {
        sources: [
          source({
            harness: 'pi',
            found: false,
            locations: [{ path: '/home/example/.pi/agent', role: 'records', found: false }],
            sessions: 35,
          }),
        ],
      },
    })
    open('/settings')
    const list = await screen.findByRole('list', { name: 'Harnesses' })
    expect(within(list).getByText('Not found')).toBeInTheDocument()
    expect(within(list).getByText('35 sessions kept from earlier')).toBeInTheDocument()
    expect(within(list).queryByText('35 sessions')).toBeNull()
    expect(screen.getByText('0 of 1 harness found on this machine.')).toBeInTheDocument()
  })

  test('records it could not understand are said beside the harness, with the evidence a step away', async () => {
    serve({ sources: SOURCES })
    open('/settings')
    const list = await screen.findByRole('list', { name: 'Harnesses' })
    const claude = within(within(list).getByRole('heading', { name: 'Claude Code' }).closest('li') as HTMLElement)
    expect(
      claude.getByText(
        /^Not understood since Nov 12, 2026: 14 records of an unknown kind and 2 unreadable lines\. Claude Code may have changed its format, so some of its records are not shown\.$/,
      ),
    ).toBeInTheDocument()
    expect(claude.getByText('Not understood in formats read earlier: 3 records of an unknown kind.')).toBeInTheDocument()

    // Only the formats with something not understood are listed; one names no version.
    const table = claude.getByRole('table', { name: 'Claude Code: what was not understood, by format' })
    const body = within(table).getAllByRole('row').slice(1)
    expect(body.map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))).toEqual([
      ['14', '2', 'Nov 12, 2026', 'Nov 16, 2026'],
      ['3', '0', 'Aug 2, 2026', 'Aug 9, 2026'],
    ])
    expect(body.map((row) => within(row).getByRole('rowheader').textContent)).toEqual(['2.1.14', 'No version named'])
    // On a phone the same formats are a list of terms and values in place of the table.
    const stacked = claude.getByRole('list', { name: 'Claude Code: what was not understood, by format' })
    expect(within(stacked).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      '2.1.14Unknown records14Unreadable lines2First readNov 12, 2026Last readNov 16, 2026',
      'No version namedUnknown records3Unreadable lines0First readAug 2, 2026Last readAug 9, 2026',
    ])
    // A harness whose records were all understood has no such table.
    const codex = within(within(list).getByRole('heading', { name: 'Codex' }).closest('li') as HTMLElement)
    expect(codex.queryByRole('table')).toBeNull()
  })

  test('a service that cannot be reached is said in the page’s own words', async () => {
    serve({ sources: 502 })
    open('/settings')
    expect(await screen.findByRole('heading', { name: "Can't reach the agdash service" }, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('Settings · Data', () => {
  test('says where the store is, how much it holds, and that nothing is removed', async () => {
    const asked = serve({ storage: STORAGE, archive: { chunks: 10_841, bytes: 2_238_184_614 } })
    open('/settings/data')
    const store = await screen.findByRole('region', { name: 'Store' })
    expect(within(store).getByText('/home/example/agdash/agdash.db')).toBeInTheDocument()
    expect(within(store).getByText('7.7 GB')).toBeInTheDocument()
    expect(within(store).getByText('7,737 sessions, the earliest started Mar 3, 2025')).toBeInTheDocument()
    // The archive's size is asked for apart from the rest, and arrives after it.
    expect(await within(store).findByText('Kept, redacted and compressed. 2.2 GB archived.')).toBeInTheDocument()
    expect(asked).toContain('/api/storage/archive')
    expect(within(store).getByText('AGDASH_KEEP_RAW=false')).toBeInTheDocument()

    const retention = screen.getByRole('region', { name: 'Retention' })
    expect(within(retention).getByText('agdash keeps every session: no limit is set.')).toBeInTheDocument()
    expect(within(retention).getByText('AGDASH_RETENTION_DAYS')).toBeInTheDocument()
    expect(within(retention).queryByText('Would remove now')).toBeNull()
    // With no limit the commands show an example one, and say so.
    expect(within(retention).getByText('agdash retention --days 90')).toBeInTheDocument()
    expect(within(retention).getByText('Lists what a limit of 90 days would remove. Removes nothing.')).toBeInTheDocument()
    // The page reports; it offers nothing that changes or removes.
    expect(within(retention).queryByRole('button')).toBeNull()
    expect(within(retention).queryByRole('textbox')).toBeNull()
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('a limit in effect says what it would remove now and what it keeps', async () => {
    serve({
      storage: {
        ...STORAGE,
        archive: { enabled: false, chunks: 0 },
        retention: { days: 30, cutoff: '2026-10-17T12:00:00Z', removable_sessions: 1204, kept_alive: 3 },
      },
    })
    open('/settings/data')
    const retention = await screen.findByRole('region', { name: 'Retention' })
    expect(within(retention).getByText(/^agdash is set to keep 30 days of history\./)).toBeInTheDocument()
    expect(within(retention).getByText('AGDASH_RETENTION_DAYS=30')).toBeInTheDocument()
    expect(within(retention).getByText(/^1,204 sessions last active before Oct 17, 2026/)).toBeInTheDocument()
    expect(within(retention).getByText('3 sessions that may still run, or belong with one that may')).toBeInTheDocument()
    expect(within(retention).getByText('agdash retention --days 30 --apply')).toBeInTheDocument()
    expect(within(retention).getByText(/This cannot be undone\.$/)).toBeInTheDocument()

    // Not kept, and nothing archived: nothing is measured, and no way to stop is offered.
    const store = screen.getByRole('region', { name: 'Store' })
    expect(within(store).getByText('Not kept for lines read from now on. Nothing is archived yet.')).toBeInTheDocument()
    // The setting that turned it off is named, and how to keep them again.
    expect(within(store).getByText('AGDASH_KEEP_RAW')).toBeInTheDocument()
    expect(within(store).getByText(/^Turned off where the agdash service starts\./)).toBeInTheDocument()
    expect(within(store).queryByText('AGDASH_KEEP_RAW=false')).toBeNull()
  })

  test('the store’s facts show while the archive is still being measured, and when it cannot be', async () => {
    serve({ storage: STORAGE, archive: 'never' })
    open('/settings/data')
    const store = await screen.findByRole('region', { name: 'Store' })
    expect(within(store).getByText('7.7 GB')).toBeInTheDocument()
    expect(within(store).getByText('Kept, redacted and compressed. Measuring what is archived.')).toBeInTheDocument()
  })

  test('export is explained and leads to the sessions', async () => {
    serve({ storage: STORAGE, archive: { chunks: 1, bytes: 1 } })
    open('/settings/data')
    const exporting = await screen.findByRole('region', { name: 'Export' })
    for (const format of ['Markdown', 'JSONL', 'Raw lines']) expect(within(exporting).getByText(format)).toBeInTheDocument()
    expect(within(exporting).getByRole('link', { name: 'Choose a session in History' })).toHaveAttribute('href', '#/history')
  })

  test('a store that is not a file names no path and no size', async () => {
    serve({ storage: { ...STORAGE, store: { ...STORAGE.store, file: null, bytes: null } }, archive: { chunks: 1, bytes: 1 } })
    open('/settings/data')
    const store = await screen.findByRole('region', { name: 'Store' })
    expect(within(store).getByText('A database server, not a file on this machine')).toBeInTheDocument()
    expect(within(store).queryByText('Size')).toBeNull()
  })
})

describe('exporting a session', () => {
  function saving() {
    const saved: { name: string; file: Blob }[] = []
    const files = new Map<string, Blob>()
    // jsdom has no object URLs; give it the two functions so they can be watched as in a browser.
    if (!('createObjectURL' in URL)) Object.assign(URL, { createObjectURL: () => '', revokeObjectURL: () => {} })
    vi.spyOn(URL, 'createObjectURL').mockImplementation((file) => {
      const address = `blob:test/${files.size}`
      files.set(address, file as Blob)
      return address
    })
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click(this: HTMLAnchorElement) {
      const file = files.get(this.href)
      if (this.download && file) saved.push({ name: this.download, file })
    })
    return saved
  }

  test('the choices open from the title line and each saves a file', async () => {
    const saved = saving()
    const asked = serve({ exports: { markdown: '# Synthetic session\n', jsonl: '{"type":"session"}\n' } })
    open('/history/sessions/1')
    const toggle = await screen.findByRole('button', { name: 'Export' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('group', { name: 'Export this session as' })).toBeNull()
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const choices = screen.getByRole('group', { name: 'Export this session as' })
    expect(toggle).toHaveAttribute('aria-controls', choices.id)

    fireEvent.click(within(choices).getByRole('button', { name: 'Markdown' }))
    await waitFor(() => expect(within(choices).getByRole('status')).toHaveTextContent('Exported agdash-session-1.md (20 bytes)'))
    expect(asked).toContain('/api/sessions/1/export?format=markdown')
    fireEvent.click(within(choices).getByRole('button', { name: 'JSONL' }))
    await waitFor(() => expect(saved.map((item) => item.name)).toEqual(['agdash-session-1.md', 'agdash-session-1.jsonl']))
    expect(await saved[0].file.text()).toBe('# Synthetic session\n')
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('a session with no archived lines saves no empty file', async () => {
    const saved = saving()
    serve({ exports: { raw: '' } })
    open('/history/sessions/1')
    fireEvent.click(await screen.findByRole('button', { name: 'Export' }))
    fireEvent.click(screen.getByRole('button', { name: 'Raw lines' }))
    expect(await screen.findByText(/^No original lines are archived for this session/)).toBeInTheDocument()
    expect(saved).toEqual([])
  })

  test('a failed export says so and can be tried again', async () => {
    const saved = saving()
    serve({ exports: { markdown: 500 } })
    open('/history/sessions/1')
    fireEvent.click(await screen.findByRole('button', { name: 'Export' }))
    fireEvent.click(screen.getByRole('button', { name: 'Markdown' }))
    expect(await screen.findByText('The export did not finish. Try again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Markdown' })).toBeEnabled()
    expect(saved).toEqual([])
  })
})

describe('sizes', () => {
  test('are told in the largest unit that keeps them at one or more', () => {
    expect(sizeParts(0)).toEqual({ unit: 'bytes', value: '0' })
    expect(sizeParts(999)).toEqual({ unit: 'bytes', value: '999' })
    expect(sizeParts(1000)).toEqual({ unit: 'kB', value: '1' })
    expect(sizeParts(412_300_000)).toEqual({ unit: 'MB', value: '412.3' })
    expect(sizeParts(7_707_283_456)).toEqual({ unit: 'GB', value: '7.7' })
    expect(sizeParts(5e15)).toEqual({ unit: 'TB', value: '5,000' })
  })
})
