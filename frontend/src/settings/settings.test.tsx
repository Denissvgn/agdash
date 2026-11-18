import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { newQueryClient } from '../api/queryClient'
import type { ArchiveSize, RetentionPreview, ServiceUsageReport, Source, SourcesReport, StorageReport, StoreParts } from '../api/types'
import { unnamedControls } from '../test/controls'
import { openTwin } from '../test/twin'
import { DESKTOP, PHONE, setWidth } from '../test/viewport'
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

// No limit, in a service that removes by itself once one is set.
const STORAGE: StorageReport = {
  store: { file: '/home/example/agdash/agdash.db', bytes: 7_707_283_456, sessions: 7737, first_started_at: '2025-03-03T08:00:00Z' },
  archive: { enabled: true, source: 'default', chunks: 10_841 },
  retention: {
    days: null,
    source: 'default',
    cutoff: null,
    removable_sessions: null,
    kept_alive: null,
    automatic: true,
    running: false,
    next_removal_at: null,
    last_removal: null,
  },
}
// A limit of 30 days saved on the page, with sessions past it.
const LIMITED: StorageReport = {
  ...STORAGE,
  retention: {
    ...STORAGE.retention,
    days: 30,
    source: 'page',
    cutoff: '2026-10-17T12:00:00Z',
    removable_sessions: 1204,
    kept_alive: 3,
    next_removal_at: '2026-11-18T09:00:00Z',
  },
}

// Six ten-second steps of a synthetic service; in the third nothing was measured.
const USAGE: ServiceUsageReport = {
  known: true,
  since: '2026-11-17T09:00:00Z',
  cpus: 12,
  range: 'hour',
  step_seconds: 10,
  measured_seconds: 50,
  now: {
    at: '2026-11-17T12:00:00Z',
    cpu_percent: 5.72,
    footprint_bytes: 157_000_000,
    resident_bytes: 48_000_000,
    read_bytes_per_second: 1_040_000,
    written_bytes_per_second: null,
    store_bytes: 7_720_000_000,
    free_bytes: 312_000_000_000,
  },
  points: [4, 6, null, 5, 8, 5.72].map((cpu, i) => ({
    at: `2026-11-17T11:59:${String(i * 10).padStart(2, '0')}Z`,
    cpu_percent: cpu,
    footprint_bytes: cpu === null ? null : 150_000_000 + i * 1_000_000,
    read_bytes_per_second: cpu === null ? null : 1_000_000,
    written_bytes_per_second: cpu === null ? null : 3_000,
    store_bytes: cpu === null ? null : 7_720_000_000,
  })),
  parts: [
    { part: 'probe', cpu_seconds: 1.55, cpu_percent: 3.1 },
    { part: 'ingest', cpu_seconds: 1.1, cpu_percent: 2.2 },
    { part: 'api', cpu_seconds: 0.2, cpu_percent: 0.4 },
  ],
}
const PARTS: StoreParts = {
  bytes: 7_720_000_000,
  archive_bytes: 2_260_000_000,
  free_bytes: 1_970_000_000,
  rest_bytes: 3_490_000_000,
}

type Served = {
  sources?: SourcesReport | number
  usage?: ServiceUsageReport | number
  parts?: StoreParts | number
  storage?: StorageReport | number
  archive?: ArchiveSize | number | 'never'
  exports?: Record<string, string | number>
  /** What a limit would remove, by its days. */
  preview?: (days: number) => RetentionPreview | number
  /** What the service answers to a saved setting; the page then shows that. */
  save?: (body: { retention_days?: number | null; keep_raw?: boolean }, now: StorageReport) => StorageReport | number
  /** What the store holds once an asked-for removal is over. */
  afterRemoval?: StorageReport
}

function serve(served: Served) {
  const asked: string[] = []
  const sent: { path: string; body: unknown }[] = []
  let storage = served.storage
  let removing = false
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://127.0.0.1:8765')
      const json = (value: unknown, status = 200) =>
        typeof value === 'number' ? new Response('{}', { status: value }) : new Response(JSON.stringify(value), { status })
      if (init?.method === 'POST') {
        const body = init.body ? JSON.parse(String(init.body)) : undefined
        sent.push({ path: url.pathname, body })
        if (typeof storage !== 'object') return json(404)
        if (url.pathname === '/api/storage/settings') {
          const answer = served.save?.(body, storage) ?? 500
          if (typeof answer === 'object') storage = answer
          return json(answer)
        }
        if (url.pathname === '/api/storage/retention/removal') {
          if (!served.afterRemoval) return json(503)
          removing = true
          storage = { ...storage, retention: { ...storage.retention, running: true } }
          return json(storage, 202)
        }
        return json(404)
      }
      asked.push(`${url.pathname}${url.search}`)
      if (url.pathname === '/api/sources' && served.sources !== undefined) return json(served.sources)
      if (url.pathname === '/api/storage' && storage !== undefined) {
        // The first look after a removal was asked for finds it over.
        if (removing && served.afterRemoval) {
          removing = false
          storage = served.afterRemoval
        }
        return json(storage)
      }
      if (url.pathname === '/api/storage/retention/preview' && served.preview) {
        return json(served.preview(Number(url.searchParams.get('days'))))
      }
      if (url.pathname === '/api/service/usage' && served.usage !== undefined) {
        const range = url.searchParams.get('range') as ServiceUsageReport['range']
        return json(typeof served.usage === 'number' ? served.usage : { ...served.usage, range })
      }
      if (url.pathname === '/api/storage/parts' && served.parts !== undefined) return json(served.parts)
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
  return { asked, sent }
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
    // A harness whose records were all understood has no such table.
    const codex = within(within(list).getByRole('heading', { name: 'Codex' }).closest('li') as HTMLElement)
    expect(codex.queryByRole('table')).toBeNull()

    // On a phone the same formats are a list of terms and values in place of the table.
    // In a browser only one of the two is shown at a time, so the page is narrowed first.
    await setWidth(PHONE)
    const stacked = claude.getByRole('list', { name: 'Claude Code: what was not understood, by format' })
    expect(within(stacked).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      '2.1.14Unknown records14Unreadable lines2First readNov 12, 2026Last readNov 16, 2026',
      'No version namedUnknown records3Unreadable lines0First readAug 2, 2026Last readAug 9, 2026',
    ])
    await setWidth(DESKTOP)
  })

  test('a service that cannot be reached is said in the page’s own words', async () => {
    serve({ sources: 502 })
    open('/settings')
    expect(await screen.findByRole('heading', { name: "Can't reach the agdash service" }, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('Settings · Data', () => {
  const retentionOf = async () => within(await screen.findByRole('region', { name: 'Retention' }))
  const limits = (retention: ReturnType<typeof within>) =>
    within(retention.getByRole('radiogroup', { name: 'Keep' }))
      .getAllByRole('radio')
      .map((radio) => `${radio.closest('label')?.textContent}${(radio as HTMLInputElement).checked ? ' ✓' : ''}`)

  test('says where the store is, how much it holds, and what is kept', async () => {
    const { asked, sent } = serve({ storage: STORAGE, archive: { chunks: 10_841, bytes: 2_238_184_614 } })
    open('/settings/data')
    const store = await screen.findByRole('region', { name: 'Store' })
    expect(within(store).getByText('/home/example/agdash/agdash.db')).toBeInTheDocument()
    expect(within(store).getByText('7.7 GB')).toBeInTheDocument()
    expect(within(store).getByText('7,737 sessions, the earliest started Mar 3, 2025')).toBeInTheDocument()
    // The archive's size is asked for apart from the rest, and arrives after it.
    expect(await within(store).findByText('A copy is kept, redacted and compressed. 2.2 GB archived.')).toBeInTheDocument()
    expect(asked).toContain('/api/storage/archive')
    expect(within(store).getByRole('checkbox', { name: "Keep a copy of the harnesses' original lines" })).toBeChecked()

    const retention = await retentionOf()
    expect(retention.getByText('agdash keeps every session: no limit is set.')).toBeInTheDocument()
    expect(limits(retention)).toEqual(['Everything ✓', '30 days', '90 days', '180 days', '365 days'])
    expect(retention.getByText('Nothing: by default agdash keeps everything')).toBeInTheDocument()
    // With no limit nothing can be removed, and nothing is offered that would.
    expect(retention.queryByRole('button')).toBeNull()
    expect(retention.queryByText('Would remove now')).toBeNull()
    expect(retention.getByText('agdash retention --days 90')).toBeInTheDocument()
    expect(sent).toEqual([])
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('a new limit shows what it would remove before it is saved, and saving removes nothing', async () => {
    const { sent } = serve({
      storage: STORAGE,
      archive: { chunks: 1, bytes: 1 },
      preview: (days) => ({ days, cutoff: '2026-10-17T12:00:00Z', removable_sessions: days === 30 ? 117 : 12, kept_alive: 2 }),
      save: (body) => ({ ...LIMITED, retention: { ...LIMITED.retention, days: body.retention_days ?? null, removable_sessions: 117 } }),
    })
    open('/settings/data')
    const retention = await retentionOf()
    fireEvent.click(retention.getByRole('radio', { name: '90 days' }))
    expect(await retention.findByText(/^12 sessions last active before Oct 17, 2026/)).toBeInTheDocument()
    // Changing one's mind costs nothing: the choice goes back and nothing was sent.
    fireEvent.click(retention.getByRole('button', { name: 'Cancel' }))
    expect(limits(retention)[0]).toBe('Everything ✓')
    expect(retention.queryByRole('button', { name: 'Save limit' })).toBeNull()

    fireEvent.click(retention.getByRole('radio', { name: '30 days' }))
    expect(await retention.findByText(/^117 sessions last active before Oct 17, 2026/)).toBeInTheDocument()
    expect(retention.getByText('Keeping 30 days would remove now')).toBeInTheDocument()
    // The last press before removal that asks no more says so, and that it cannot be undone.
    expect(
      retention.getByText(/^Saving removes nothing now\. From an hour after you save, .* without asking again, and they cannot be brought back\./),
    ).toBeInTheDocument()
    expect(sent).toEqual([])
    fireEvent.click(retention.getByRole('button', { name: 'Save limit' }))
    expect(await retention.findByText(/^Saved\. Nothing was removed; the next daily removal is on Nov 18, 2026/)).toBeInTheDocument()
    expect(sent).toEqual([{ path: '/api/storage/settings', body: { retention_days: 30 } }])
    expect(retention.getByText('agdash keeps 30 days of history. Sessions past that are removed once a day.')).toBeInTheDocument()
    expect(limits(retention)).toEqual(['Everything', '30 days ✓', '90 days', '180 days', '365 days'])
    expect(retention.getByText('This page')).toBeInTheDocument()
    expect(retention.queryByRole('button', { name: 'Save limit' })).toBeNull()
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('keeping everything again is a choice that is saved too', async () => {
    const { sent } = serve({ storage: LIMITED, archive: { chunks: 1, bytes: 1 }, save: () => STORAGE })
    open('/settings/data')
    const retention = await retentionOf()
    fireEvent.click(retention.getByRole('radio', { name: 'Everything' }))
    expect(retention.getByText('Keeping everything removes nothing, and ends the daily removal.')).toBeInTheDocument()
    fireEvent.click(retention.getByRole('button', { name: 'Save limit' }))
    expect(await retention.findByText('agdash keeps every session: no limit is set.')).toBeInTheDocument()
    expect(sent).toEqual([{ path: '/api/storage/settings', body: { retention_days: null } }])
  })

  test('a limit is saved only once what it would remove has been shown', async () => {
    let working = false
    const { sent } = serve({
      storage: STORAGE,
      archive: { chunks: 1, bytes: 1 },
      preview: (days) => (working ? { days, cutoff: '2026-10-17T12:00:00Z', removable_sessions: 117, kept_alive: 2 } : 500),
      save: () => 500,
    })
    open('/settings/data')
    const retention = await retentionOf()
    fireEvent.click(retention.getByRole('radio', { name: '30 days' }))
    // While it is worked out, and when it cannot be, there is nothing to agree to yet.
    expect(retention.getByRole('button', { name: 'Save limit' })).toBeDisabled()
    expect(await retention.findByText(/could not be worked out, so it cannot be saved yet\.$/, {}, { timeout: 4000 })).toBeInTheDocument()
    expect(retention.getByRole('button', { name: 'Save limit' })).toBeDisabled()
    working = true
    fireEvent.click(retention.getByRole('button', { name: 'Try again' }))
    expect(await retention.findByText(/^117 sessions last active before Oct 17, 2026/)).toBeInTheDocument()
    expect(retention.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(retention.getByRole('button', { name: 'Save limit' })).toBeEnabled()

    fireEvent.click(retention.getByRole('button', { name: 'Save limit' }))
    expect(await retention.findByText('The limit was not saved. Try again.')).toBeInTheDocument()
    expect(retention.getByRole('button', { name: 'Save limit' })).toBeEnabled()
    expect(sent).toHaveLength(1)
  })

  test('a limit in effect says what it would remove, what it keeps and when it next removes', async () => {
    serve({ storage: LIMITED, archive: { chunks: 1, bytes: 1 } })
    open('/settings/data')
    const retention = await retentionOf()
    expect(retention.getByText(/^1,204 sessions last active before Oct 17, 2026/)).toBeInTheDocument()
    // Nothing is being tried, so nothing speaks of a limit that would be.
    expect(retention.queryByText(/^Keeping .* would remove now$/)).toBeNull()
    expect(retention.getByText('3 sessions that may still run, or belong with one that may')).toBeInTheDocument()
    expect(retention.getByText(/^Once a day by the agdash service, next on Nov 18, 2026/)).toBeInTheDocument()
    expect(retention.getByText('Nothing has been removed yet')).toBeInTheDocument()
    expect(retention.getByText('agdash retention --apply')).toBeInTheDocument()
  })

  test('removing now is asked twice, says what goes, and is followed to its end', async () => {
    const { asked, sent } = serve({
      storage: LIMITED,
      archive: { chunks: 1, bytes: 1 },
      afterRemoval: {
        ...LIMITED,
        retention: {
          ...LIMITED.retention,
          removable_sessions: 0,
          last_removal: { at: '2026-11-17T09:05:00Z', sessions: 1204, events: 52_000, automatic: false, error: null },
        },
      },
    })
    open('/settings/data')
    const retention = await retentionOf()
    const looks = () => asked.filter((path) => path === '/api/storage').length
    const before = looks()
    fireEvent.click(retention.getByRole('button', { name: 'Remove 1,204 sessions now' }))
    expect(retention.getByText(/^Remove 1,204 sessions last active before Oct 17, 2026.*\?$/)).toBeInTheDocument()
    // The question is asked of what the store holds now: the page looks again as it opens.
    await waitFor(() => expect(looks()).toBe(before + 1))
    expect(retention.getByText(/cannot be brought back\. The harnesses' own files are not touched\.$/)).toBeInTheDocument()
    // The safe answer has the focus, and taking it sends nothing.
    await waitFor(() => expect(retention.getByRole('button', { name: 'Keep them' })).toHaveFocus())
    fireEvent.click(retention.getByRole('button', { name: 'Keep them' }))
    expect(retention.queryByRole('button', { name: 'Keep them' })).toBeNull()
    expect(sent).toEqual([])

    fireEvent.click(retention.getByRole('button', { name: 'Remove 1,204 sessions now' }))
    await waitFor(() => expect(retention.getByRole('button', { name: 'Remove 1,204 sessions' })).toBeEnabled())
    fireEvent.click(retention.getByRole('button', { name: 'Remove 1,204 sessions' }))
    expect(await retention.findByText(/^Removing the sessions past the limit\./)).toBeInTheDocument()
    // The time the question showed goes with the request, so nothing later can be taken.
    expect(sent).toEqual([{ path: '/api/storage/retention/removal', body: { before: '2026-10-17T12:00:00Z' } }])
    // With the question gone, the focus stays where it was asked. The page can show that the
    // removal runs, from a look it had already begun, a moment before the answer moves the focus.
    await waitFor(() => expect(retention.getByRole('heading', { name: 'Remove now' })).toHaveFocus())
    // The page asks again by itself until it is over, then says what went.
    expect(await retention.findByText(/: 1,204 sessions with 52,000 events$/, {}, { timeout: 6000 })).toBeInTheDocument()
    expect(retention.getByText('Nothing is past the limit now.')).toBeInTheDocument()
    expect(retention.queryByText(/^Removing the sessions/)).toBeNull()
    expect(unnamedControls(document.body)).toEqual([])
  }, 10_000)

  test('a service that removes nothing by itself offers the command, not a button', async () => {
    serve({
      storage: { ...LIMITED, retention: { ...LIMITED.retention, source: 'environment', days: 45, automatic: false, next_removal_at: null } },
      archive: { chunks: 1, bytes: 1 },
    })
    open('/settings/data')
    const retention = await retentionOf()
    expect(retention.getByText(/^agdash is set to keep 45 days of history\. Nothing is removed until you run the command below\.$/)).toBeInTheDocument()
    // A limit set where the service starts joins the choices, and is named as what it is.
    expect(limits(retention)).toEqual(['Everything', '30 days', '45 days ✓', '90 days', '180 days', '365 days'])
    expect(retention.getByText('AGDASH_RETENTION_DAYS=45')).toBeInTheDocument()
    expect(retention.getByText('Only by the command below: this service removes nothing by itself')).toBeInTheDocument()
    expect(retention.queryByRole('heading', { name: 'Remove now' })).toBeNull()
    expect(retention.queryByRole('button')).toBeNull()
  })

  test('a removal that did not finish is said, with its kind', async () => {
    serve({
      storage: {
        ...LIMITED,
        retention: {
          ...LIMITED.retention,
          last_removal: { at: '2026-11-17T09:05:00Z', sessions: 0, events: 0, automatic: true, error: 'OperationalError' },
        },
      },
      archive: { chunks: 1, bytes: 1 },
    })
    open('/settings/data')
    const retention = await retentionOf()
    expect(retention.getByText(/: the removal did not finish \(OperationalError\)\. The next one tries again\.$/)).toBeInTheDocument()
  })

  test('keeping original lines is switched with a checkbox and saved as it is changed', async () => {
    const { sent } = serve({
      storage: STORAGE,
      archive: { chunks: 10_841, bytes: 2_238_184_614 },
      save: (body, now) => ({ ...now, archive: { ...now.archive, enabled: body.keep_raw ?? true, source: 'page' } }),
    })
    open('/settings/data')
    const store = await screen.findByRole('region', { name: 'Store' })
    fireEvent.click(within(store).getByRole('checkbox', { name: "Keep a copy of the harnesses' original lines" }))
    // What is already archived stays, and is still measured.
    expect(await within(store).findByText('No copy is kept of lines read from now on. 2.2 GB archived.')).toBeInTheDocument()
    expect(within(store).getByText(/^Saved\. It holds from the next lines read/)).toBeInTheDocument()
    expect(within(store).getByRole('checkbox')).not.toBeChecked()
    expect(sent).toEqual([{ path: '/api/storage/settings', body: { keep_raw: false } }])
  })

  test('the store’s facts show while the archive is still being measured', async () => {
    serve({ storage: STORAGE, archive: 'never' })
    open('/settings/data')
    const store = await screen.findByRole('region', { name: 'Store' })
    expect(within(store).getByText('7.7 GB')).toBeInTheDocument()
    expect(within(store).getByText('A copy is kept, redacted and compressed. Measuring what is archived.')).toBeInTheDocument()
  })

  test('an archive set where the service starts names the setting', async () => {
    serve({ storage: { ...STORAGE, archive: { enabled: false, source: 'environment', chunks: 0 } } })
    open('/settings/data')
    const store = await screen.findByRole('region', { name: 'Store' })
    expect(within(store).getByText('No copy is kept of lines read from now on. Nothing is archived yet.')).toBeInTheDocument()
    expect(within(store).getByText('AGDASH_KEEP_RAW')).toBeInTheDocument()
    expect(within(store).getByRole('checkbox')).not.toBeChecked()
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
    const { asked } = serve({ exports: { markdown: '# Synthetic session\n', jsonl: '{"type":"session"}\n' } })
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

describe('Settings · Service', () => {
  const rowsOf = (figure: HTMLElement) =>
    within(figure)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))

  test('says what the service uses now, as facts and never as tiles', async () => {
    serve({ usage: USAGE })
    open('/settings/service')
    const now = within(await screen.findByRole('region', { name: 'Now' }))
    expect(now.getByText('5.7% of one core')).toBeInTheDocument()
    expect(now.getByText('This machine has 12 cores.')).toBeInTheDocument()
    expect(now.getByText('157 MB')).toBeInTheDocument()
    expect(now.getByText(/^48 MB of it is resident\./)).toBeInTheDocument()
    expect(now.getByText('1 MB a second')).toBeInTheDocument()
    // A number the system did not give is said to be missing, never shown as nothing written.
    expect(now.getByText('Not reported')).toBeInTheDocument()
    expect(now.getByText('7.7 GB')).toBeInTheDocument()
    expect(now.getByText('312 GB free on its volume.')).toBeInTheDocument()
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('draws each measure over time with a table of the same values', async () => {
    serve({ usage: USAGE })
    open('/settings/service')
    const over = await screen.findByRole('region', { name: 'Over time' })
    const figures = within(over).getAllByRole('figure')
    expect(figures.map((figure) => within(figure).getByRole('heading').textContent)).toEqual([
      'Processor, % of one core',
      'Memory',
      'Read from disk, each second',
      'Written to disk, each second',
      'Store size',
    ])
    openTwin(over)
    const cpu = rowsOf(figures[0])
    expect(cpu.map((cells) => cells[0])).toEqual([
      '4% of one core',
      '6% of one core',
      '–not reported',
      '5% of one core',
      '8% of one core',
      '5.7% of one core',
    ])
    expect(rowsOf(figures[1])[0]).toEqual(['150 MB'])
    expect(rowsOf(figures[2])[0]).toEqual(['1 MB a second'])
    expect(rowsOf(figures[3])[0]).toEqual(['3 kB a second'])
  })

  test('ranks the parts of the service by the processor time they took', async () => {
    serve({ usage: USAGE })
    open('/settings/service')
    const parts = await screen.findByRole('region', { name: 'By part' })
    openTwin(parts)
    const rows = within(parts).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent)).toEqual([
      'Watching the process table3.1% of one core',
      'Reading harness files2.2% of one core',
      'Answering pages0.4% of one core',
    ])
  })

  test('keeps the range in the address, and the hour out of it', async () => {
    const { asked } = serve({ usage: USAGE })
    open('/settings/service')
    const range = within(await screen.findByRole('radiogroup', { name: 'Show' }))
    expect(range.getByRole('radio', { name: 'The last hour' })).toBeChecked()
    expect(asked).toContain('/api/service/usage?range=hour')
    fireEvent.click(range.getByRole('radio', { name: 'The last 90 days' }))
    await waitFor(() => expect(asked).toContain('/api/service/usage?range=quarter'))
    expect(window.location.hash).toBe('#/settings/service?range=quarter')
    fireEvent.click(range.getByRole('radio', { name: 'The last hour' }))
    await waitFor(() => expect(window.location.hash).toBe('#/settings/service'))
  })

  test('a copy that does not measure itself says so', async () => {
    serve({ usage: { ...USAGE, known: false, since: null } })
    open('/settings/service')
    const now = within(await screen.findByRole('region', { name: 'Now' }))
    expect(now.getByText(/^This copy of the service does not measure itself\. These are the last figures kept/)).toBeInTheDocument()
    expect(now.queryByText('Measuring since')).toBeNull()
  })

  test('before the first sample it says nothing is measured yet', async () => {
    serve({ usage: { ...USAGE, now: null, parts: [], points: USAGE.points.map((point) => ({ ...point, cpu_percent: null })) } })
    open('/settings/service')
    const now = within(await screen.findByRole('region', { name: 'Now' }))
    expect(now.getByText(/^Nothing is measured yet\./)).toBeInTheDocument()
  })

  test('says so in its own words when the service cannot be reached', async () => {
    serve({ usage: 503 })
    open('/settings/service')
    // The page asks once more before it gives up.
    expect(await screen.findByText("Can't reach the agdash service", undefined, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('Settings · Data: what the store holds', () => {
  test('lists the parts of the file, the largest first, with their share', async () => {
    const { asked } = serve({ storage: STORAGE, archive: { chunks: 10_841, bytes: 2_260_000_000 }, parts: PARTS })
    open('/settings/data')
    const store = await screen.findByRole('region', { name: 'Store' })
    const table = within(await within(store).findByRole('region', { name: 'What the store holds' })).getByRole('table')
    expect(
      within(table)
        .getAllByRole('row')
        .slice(1)
        .map((row) => [within(row).getByRole('rowheader').textContent, ...within(row).getAllByRole('cell').map((cell) => cell.textContent)]),
    ).toEqual([
      ['Events, their search index and the rest', '3.5 GB', '45%'],
      ['Original lines', '2.3 GB', '29%'],
      ['Free inside the file', '2 GB', '26%'],
    ])
    expect(asked).toContain('/api/storage/parts')
    expect(unnamedControls(document.body)).toEqual([])
  })

  test('says so when the parts could not be measured', async () => {
    serve({ storage: STORAGE, parts: 500 })
    open('/settings/data')
    const store = await screen.findByRole('region', { name: 'Store' })
    // The page asks once more before it gives up.
    expect(await within(store).findByText('What the store holds could not be measured.', {}, { timeout: 4000 })).toBeInTheDocument()
  })

  test('leaves out a part that cannot be told, rather than calling it nothing', async () => {
    serve({ storage: STORAGE, parts: { ...PARTS, free_bytes: null } })
    open('/settings/data')
    const table = within(await screen.findByRole('region', { name: 'What the store holds' })).getByRole('table')
    expect(within(table).getAllByRole('rowheader').map((header) => header.textContent)).toEqual([
      'Events, their search index and the rest',
      'Original lines',
    ])
  })
})
