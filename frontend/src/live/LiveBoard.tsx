import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleAlert } from 'lucide-react'
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { getJson } from '../api/client'
import type { LiveActivity, Page, SessionState, SessionSummary } from '../api/types'
import { STRIP_STATES } from '../charts/states'
import { heatScale } from '../charts/heat'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows, Notice, StateDot } from '../history/SessionList'
import { ReadingLine } from '../shell/ReadingLine'
import { AgentGraph } from './AgentGraph'
import {
  DENSE_ABOVE,
  ENDED_WINDOW_MS,
  LIVE_GROUPS,
  LIVE_STATES,
  LIVE_VIEWS,
  QUIET_AFTER_MS,
  UNKNOWN_WINDOW_MS,
  flatten,
  forest,
  groupNodes,
  isLongQuiet,
  keep,
  split,
  stateCounts,
  type LiveGroup,
  type LiveView,
  type Placed,
} from './board'
import type { LinkTo, Strips } from './parts'
import { Roster } from './Roster'
import { SessionPanel } from './SessionPanel'
import { useLiveStream, type StreamMessage, type StreamStatus } from './stream'
import { StripsKey } from './Strips'

const TICK_MS = 30_000
const REFETCH_DELAY_MS = 300
/** How far back to look for sessions that ended recently, whatever their last activity. */
const ENDED_LOOKBACK_MS = 12 * 60 * 60_000

function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(timer)
  }, [])
  return now
}

async function liveSessions(signal: AbortSignal): Promise<SessionSummary[]> {
  const now = Date.now()
  const iso = (ms: number) => new Date(now - ms).toISOString()
  const pages = await Promise.all([
    getJson<Page<SessionSummary>>('/api/sessions', { state: ['active', 'waiting', 'idle'], limit: 200 }, signal),
    getJson<Page<SessionSummary>>(
      '/api/sessions',
      { state: 'unknown', active_since: iso(UNKNOWN_WINDOW_MS), limit: 200 },
      signal,
    ),
    getJson<Page<SessionSummary>>(
      '/api/sessions',
      { state: 'ended', active_since: iso(ENDED_LOOKBACK_MS), limit: 200 },
      signal,
    ),
  ])
  return pages.flatMap((page) => page.items)
}

const CHOSEN =
  'toggle-chip relative cursor-pointer has-[:checked]:border-planning-thread has-[:checked]:bg-planning-thread-soft has-[:checked]:text-planning-thread has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-planning-thread'

/** One of a few ways to see the board, as a row of pills: how it is grouped, or drawn. */
function Choice<T extends string>({
  legend,
  options,
  value,
  label,
  onChange,
}: {
  legend: string
  options: T[]
  value: T
  label: (option: T) => string
  onChange: (option: T) => void
}) {
  const name = useId()
  return (
    // A labelled radio group on one line: a fieldset's legend would take a line of its own.
    <div aria-labelledby={`${name}-legend`} className="flex flex-wrap items-center gap-sm" role="radiogroup">
      <span className="text-label text-secondary-ink" id={`${name}-legend`}>
        {legend}
      </span>
      {options.map((option) => (
        <label key={option} className={CHOSEN}>
          <input checked={value === option} className="sr-only" name={name} type="radio" value={option} onChange={() => onChange(option)} />
          {label(option)}
        </label>
      ))}
    </div>
  )
}

/** The live count as one line: said for assistive technology, and shown as pills that filter the board by state. */
function Counts({
  live,
  quiet,
  shown,
  onToggle,
}: {
  live: SessionSummary[]
  quiet: boolean
  shown: ReadonlySet<SessionState>
  onToggle: (state: SessionState) => void
}) {
  const { t } = useTranslation()
  const counts = stateCounts(live)
  return (
    <>
      <p aria-live="polite" className="sr-only" role="status">
        {live.length === 0
          ? t('live.summary.none')
          : t('live.summary.live', {
              count: live.length,
              states: counts.map(([state, count]) => t(`live.summary.state.${state}`, { count })).join(' · '),
            })}
      </p>
      {quiet ? null : (
        <div aria-label={t('live.filter.label')} className="flex flex-wrap items-center gap-sm" role="group">
          <span aria-hidden="true" className="mr-xs text-body text-primary-ink">
            {t('live.filter.count', { count: live.length })}
          </span>
          {counts.map(([state, count]) => (
            <button key={state} aria-pressed={shown.has(state)} className="toggle-chip" type="button" onClick={() => onToggle(state)}>
              <StateDot state={state} />
              {t(`live.summary.state.${state}`, { count })}
            </button>
          ))}
        </div>
      )}
    </>
  )
}

/** What the board is and how to read it: said once, out of the way of the rows. */
function About({ status }: { status: StreamStatus }) {
  const { t } = useTranslation()
  return (
    <details className="text-label text-secondary-ink">
      <summary className="cursor-pointer">{t('live.about.summary')}</summary>
      <div className="mt-xs flex max-w-[90ch] flex-col gap-xs">
        <p>{t(`live.stream.${status}`)}</p>
        <p>{t('live.strips.key')}</p>
        <p>{t('live.unknownWindow', { minutes: UNKNOWN_WINDOW_MS / 60_000 })}</p>
        <p>{t('live.quiet.about', { hours: QUIET_AFTER_MS / 3_600_000 })}</p>
        <p>{t('live.ended.body', { minutes: ENDED_WINDOW_MS / 60_000 })}</p>
      </div>
    </details>
  )
}

export function LiveBoardPage() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [search, setSearch] = useSearchParams()
  const by: LiveGroup = search.get('group') === 'harness' ? 'harness' : 'folder'
  const view: LiveView = search.get('view') === 'agents' ? 'agents' : 'timeline'
  const picked = Number(search.get('session')) || null
  const shownText = search.get('show') ?? ''
  const shown = useMemo(
    () => new Set(shownText.split(',').filter((state): state is SessionState => LIVE_STATES.includes(state as SessionState))),
    [shownText],
  )
  const now = useNow()
  const baseId = useId()

  const query = useQuery({ queryKey: ['live', 'sessions'], queryFn: ({ signal }) => liveSessions(signal) })
  // Replaced in place when the stream says a minute passed or a session wrote; never animated.
  const activity = useQuery({
    queryKey: ['live', 'activity'],
    queryFn: ({ signal }) => getJson<LiveActivity>('/api/live/activity', {}, signal),
  })

  const pending = useRef<{
    sessions: boolean
    activity: boolean
    prompts: Set<number>
    timer?: ReturnType<typeof setTimeout>
  }>({
    sessions: false,
    activity: false,
    prompts: new Set(),
  })
  const onMessage = useCallback(
    (message: StreamMessage) => {
      const queue = pending.current
      const data = message.data as { sessions?: { id: number }[] }
      // On (re)connecting the stream starts from now, so anything missed while it was
      // away is fetched again, and a board showing an error recovers by itself.
      if (message.event === 'hello') {
        void client.invalidateQueries({ queryKey: ['live'] })
        return
      }
      if (message.event === 'sessions') queue.sessions = true
      else if (message.event === 'events') data.sessions?.forEach(({ id }) => queue.prompts.add(id))
      else if (message.event === 'activity') queue.activity = true
      else return
      if (queue.timer) return
      queue.timer = setTimeout(() => {
        if (queue.sessions) void client.invalidateQueries({ queryKey: ['live', 'sessions'] })
        if (queue.activity) void client.invalidateQueries({ queryKey: ['live', 'activity'] })
        queue.prompts.forEach((id) => void client.invalidateQueries({ queryKey: ['live', 'prompt', id] }))
        queue.sessions = false
        queue.activity = false
        queue.prompts.clear()
        queue.timer = undefined
      }, REFETCH_DELAY_MS)
    },
    [client],
  )
  useEffect(() => () => clearTimeout(pending.current.timer), [])
  const status = useLiveStream(onMessage)

  const { live, ended } = useMemo(() => split(query.data ?? [], now), [query.data, now])
  const { arranged, quiet, placed } = useMemo(() => {
    const roots = keep(forest(live), shown)
    const quiet = roots.filter((node) => isLongQuiet(node, now))
    const current = roots.filter((node) => !quiet.includes(node))
    const arranged = groupNodes(current, by)
    const endedNodes = ended.map((session) => ({ session, children: [], orphan: false }))
    // The order the rows are in on the page, which the keys and the first pick follow.
    const placed: Placed[] = [...flatten(arranged.flatMap((group) => group.nodes)), ...flatten(quiet), ...flatten(endedNodes)]
    return { arranged, quiet, placed }
  }, [live, ended, shown, by, now])
  const strips = useMemo((): Strips | undefined => {
    const data = activity.data
    if (!data?.minutes.length) return undefined
    return {
      rows: new Map(data.sessions.map((row) => [row.id, row])),
      // One scale for the whole board, so a darker cell means more events in any row.
      scale: heatScale(data.sessions.flatMap((row) => row.events)),
      from: Date.parse(data.minutes[0]),
      until: Date.parse(data.until),
    }
  }, [activity.data])

  const change = (patch: Record<string, string | null>) => {
    const params = new URLSearchParams(search)
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) params.delete(key)
      else params.set(key, value)
    }
    return params
  }
  const to: LinkTo = (sessionId) => {
    const text = change({ session: sessionId === null ? null : String(sessionId) }).toString()
    return text ? `?${text}` : '.'
  }
  const toggle = (state: SessionState) => {
    const next = new Set(shown)
    if (next.has(state)) next.delete(state)
    else next.add(state)
    setSearch(change({ show: next.size ? LIVE_STATES.filter((value) => next.has(value)).join(',') : null }), { replace: true })
  }

  // Beside the rows the panel always shows a session: the one picked, or else the first.
  // Below 1024px it takes the rows' place, so there it shows only what was picked.
  const chosen = placed.find((row) => row.node.session.id === picked) ?? null
  const inPanel = chosen ?? placed[0] ?? null
  const dense = placed.length > DENSE_ABOVE
  // The states some lane on the board draws, for its key.
  const drawn = useMemo(() => {
    const found = new Set<string>()
    for (const row of activity.data?.sessions ?? []) for (const span of row.states) found.add(span.state)
    return STRIP_STATES.filter((state) => found.has(state))
  }, [activity.data])

  // Below 1024px the panel and the rows take each other's place, so focus goes with them.
  const before = useRef(picked)
  useEffect(() => {
    const was = before.current
    before.current = picked
    if (was === picked || !window.matchMedia?.('(max-width: 1023px)').matches) return
    const target =
      picked !== null
        ? document.querySelector<HTMLElement>('[data-panel-title]')
        : document.querySelector<HTMLElement>(`[data-roster] [data-session="${was}"] a[data-row], [data-roster] a[data-row][data-session="${was}"]`)
    target?.focus()
  }, [picked])
  const empty = live.length === 0 && ended.length === 0

  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey="workspaces.live.destinations.board.purpose" titleKey="workspaces.live.destinations.board.title" />
      <ReadingLine />
      {query.isPending ? (
        <LoadingRows label={t('live.loading')} />
      ) : query.isError ? (
        <ErrorNotice error={query.error} scope="live.error" onRetry={() => void query.refetch()} />
      ) : (
        <div className="flex flex-col gap-md">
          {/* With a session picked below 1024px only its panel shows, and these would do nothing. */}
          <div className={`flex flex-wrap items-center gap-x-lg gap-y-sm ${chosen ? 'max-[1023px]:hidden' : ''}`}>
            <Counts live={live} quiet={empty} shown={shown} onToggle={toggle} />
            {live.length > 0 && (
              <div className="flex flex-wrap items-center gap-x-lg gap-y-sm min-[1024px]:ml-auto">
                <Choice
                  label={(option) => t(`live.views.${option}`)}
                  legend={t('live.view')}
                  options={LIVE_VIEWS}
                  value={view}
                  onChange={(next) => setSearch(change({ view: next === 'timeline' ? null : next }), { replace: true })}
                />
                <Choice
                  label={(option) => t(`live.groups.${option}`)}
                  legend={t('live.groupBy')}
                  options={LIVE_GROUPS}
                  value={by}
                  onChange={(next) => setSearch(change({ group: next === 'folder' ? null : next }))}
                />
              </div>
            )}
          </div>
          {status === 'retrying' && (
            <p className="flex items-center gap-xs text-label text-secondary-ink">
              <CircleAlert aria-hidden="true" className="text-feedback-warning" size={14} strokeWidth={1.75} />
              {t('live.stream.retrying')}
            </p>
          )}
          {empty ? (
            <Notice body={t('live.empty.body')} title={t('live.empty.title')} />
          ) : placed.length === 0 ? (
            <Notice body={t('live.filter.noneBody')} title={t('live.filter.noneTitle')} />
          ) : (
            <div className="grid items-start gap-lg min-[1024px]:grid-cols-[minmax(0,1fr)_18rem] min-[1340px]:grid-cols-[minmax(0,1fr)_20rem]">
              <div className={`flex min-w-0 flex-col gap-sm ${chosen ? 'max-[1023px]:hidden' : ''}`}>
                <Roster
                  baseId={baseId}
                  by={by}
                  dense={dense}
                  ended={ended}
                  groups={arranged}
                  now={now}
                  quiet={quiet}
                  selected={inPanel?.node.session.id ?? null}
                  strips={strips}
                  to={to}
                >
                  {view === 'agents' ? (
                    <AgentGraph baseId={baseId} by={by} groups={arranged} selected={inPanel?.node.session.id ?? null} to={to} />
                  ) : undefined}
                </Roster>
                {strips && view === 'timeline' ? <StripsKey scale={strips.scale} states={drawn} /> : null}
              </div>
              {inPanel ? (
                <div className={chosen ? '' : 'max-[1023px]:hidden'}>
                  <SessionPanel
                    ended={inPanel.node.session.state === 'ended'}
                    node={inPanel.node}
                    now={now}
                    parent={inPanel.parent}
                    strips={strips}
                    to={to}
                  />
                </div>
              ) : null}
            </div>
          )}
          <About status={status} />
        </div>
      )}
    </article>
  )
}
