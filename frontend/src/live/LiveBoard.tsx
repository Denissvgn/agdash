import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleAlert } from 'lucide-react'
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { getJson } from '../api/client'
import type { LiveActivity, Page, SessionSummary } from '../api/types'
import { heatScale, type HeatScale } from '../charts/heat'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows, Notice, StateChip } from '../history/SessionList'
import {
  ENDED_WINDOW_MS,
  LIVE_GROUPS,
  UNKNOWN_WINDOW_MS,
  endedAt,
  groups,
  lastActive,
  split,
  stateCounts,
  type LiveGroup,
  type Node,
} from './board'
import { formatAgo } from './format'
import { useLiveStream, type StreamMessage, type StreamStatus } from './stream'
import { RowStrips, StripsKey, type LiveRow } from './Strips'
import { ReadingLine } from '../shell/ReadingLine'

const TICK_MS = 30_000
const REFETCH_DELAY_MS = 300
/** How far back to look for sessions that ended recently, whatever their last activity. */
const ENDED_LOOKBACK_MS = 12 * 60 * 60_000

interface LatestPrompt {
  kind: string
  payload: { text?: string }
}

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

function useLatestPrompt(sessionId: number) {
  return useQuery({
    queryKey: ['live', 'prompt', sessionId],
    queryFn: ({ signal }) =>
      getJson<Page<LatestPrompt>>(
        `/api/sessions/${sessionId}/events`,
        { kind: 'user_prompt', order: 'desc', limit: 1 },
        signal,
      ),
    staleTime: Infinity,
  })
}

function GroupSwitch({ group, onChange }: { group: LiveGroup; onChange: (group: LiveGroup) => void }) {
  const { t } = useTranslation()
  const name = useId()
  return (
    <fieldset className="flex flex-wrap items-center gap-sm">
      <legend className="float-left mr-sm text-label text-secondary-ink">{t('live.groupBy')}</legend>
      {LIVE_GROUPS.map((value) => (
        <label
          key={value}
          className="toggle-chip relative cursor-pointer has-[:checked]:border-planning-thread has-[:checked]:bg-planning-thread-soft has-[:checked]:text-planning-thread has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-planning-thread"
        >
          <input
            checked={group === value}
            className="sr-only"
            name={name}
            type="radio"
            value={value}
            onChange={() => onChange(value)}
          />
          {t(`live.groups.${value}`)}
        </label>
      ))}
    </fieldset>
  )
}

function Folder({ path }: { path: string }) {
  // Cut from the start: the last folders are what tell sessions apart.
  return (
    <span className="data block min-w-0 truncate text-left font-mono text-[0.75rem] [direction:rtl]">
      <bdi>{path}</bdi>
    </span>
  )
}

function Prompt({ sessionId }: { sessionId: number }) {
  const { t } = useTranslation()
  const { data, isPending } = useLatestPrompt(sessionId)
  const text = data?.items[0]?.payload.text?.trim()
  return (
    // The clamp sits on the paragraph so the label and the prompt share its two lines.
    <p className="line-clamp-2 max-w-[90ch] break-words text-body text-secondary-ink">
      <span className="mr-sm text-label">{t('live.row.latestPrompt')}</span>
      {isPending ? (
        // A still placeholder holds the line, so the row does not grow when the prompt arrives.
        <span aria-hidden="true" className="inline-block h-[10px] w-2/5 rounded-compact bg-canvas-mist align-middle" />
      ) : text ? (
        <span className="text-primary-ink">{text}</span>
      ) : (
        <span>{t('live.row.noPrompt')}</span>
      )}
    </p>
  )
}

// A nested row draws a hairline elbow from its parent's column, one step in.
const NESTED =
  'border-t border-hairline before:absolute before:-left-[12px] before:top-0 before:h-[23px] before:w-[26px] before:rounded-bl-compact before:border-b before:border-l before:border-hairline after:absolute after:-left-[12px] after:bottom-0 after:top-0 after:border-l after:border-hairline last:after:hidden'

/** The last 30 minutes of the board's sessions, for the rows' strips. */
type Strips = { rows: Map<number, LiveRow>; scale: HeatScale; from: number; until: number }

function Row({
  node,
  now,
  showFolder,
  strips,
  parent,
  ended = false,
  nested = false,
}: {
  node: Node
  now: number
  showFolder: boolean
  strips?: Strips
  /** The session this one was started by, when it is on the board. */
  parent?: SessionSummary
  ended?: boolean
  nested?: boolean
}) {
  const { t } = useTranslation()
  const { session } = node
  const harness = t(`history.harnesses.${session.harness}`, { defaultValue: session.harness })
  const surface = session.surface ? t(`history.surfaces.${session.surface}`) : null
  const model = session.models.at(-1)
  const basis = session.state_basis ? t(`live.basis.${session.state_basis}`, { defaultValue: '' }) : ''
  const when = ended ? endedAt(session) : lastActive(session)
  const row = strips?.rows.get(session.id)
  // An edge from a session of another harness names how the link is known.
  const across = parent && parent.harness !== session.harness
  return (
    <li className={nested ? `relative ${NESTED}` : 'relative'}>
      <div className="flex flex-col gap-xs px-md py-[12px]">
        {across ? (
          <p className="text-[0.75rem] text-secondary-ink" data-edge={session.parent_basis ?? 'unknown'}>
            {t('live.row.edge', {
              harness: t(`history.harnesses.${parent.harness}`, { defaultValue: parent.harness }),
              basis: t(`live.parentBasis.${session.parent_basis ?? 'unknown'}`, {
                defaultValue: t('live.parentBasis.unknown'),
              }),
            })}
          </p>
        ) : null}
        {/* Aligned to the title's first line, so a nested row's elbow meets the chip. */}
        <div className="flex flex-wrap items-start gap-x-sm gap-y-xs">
          <StateChip state={session.state} />
          <h3 className="line-clamp-2 min-w-[min(100%,12rem)] flex-1 break-words text-body font-semibold text-primary-ink roomy:line-clamp-none roomy:truncate">
            {session.title || t('history.list.untitled')}
          </h3>
          <span className="data shrink-0 pt-[2px] text-label text-secondary-ink">
            {when
              ? t(ended ? 'live.row.ended' : 'live.row.lastActive', { when: formatAgo(when, now) })
              : t('history.list.noActivity')}
          </span>
        </div>
        <p className="flex flex-wrap gap-x-sm text-label text-secondary-ink">
          <span>{surface ? t('history.list.harnessSurface', { harness, surface }) : harness}</span>
          <span aria-hidden="true">·</span>
          <span className="min-w-0 truncate">{model ?? t('history.list.noModel')}</span>
          {session.effort && (
            <>
              <span aria-hidden="true">·</span>
              <span>{t('live.row.effort', { effort: session.effort })}</span>
            </>
          )}
          {basis && (
            <>
              <span aria-hidden="true">·</span>
              <span>{basis}</span>
            </>
          )}
          {node.orphan && (
            <>
              <span aria-hidden="true">·</span>
              <span>{t('live.row.parentNotLive')}</span>
            </>
          )}
        </p>
        {!ended && row && strips ? <RowStrips from={strips.from} row={row} scale={strips.scale} until={strips.until} /> : null}
        <Prompt sessionId={session.id} />
        {showFolder && session.cwd && (
          <p className="text-secondary-ink">
            <Folder path={session.cwd} />
          </p>
        )}
      </div>
      {node.children.length > 0 && (
        <ul aria-label={t('live.row.startedBy', { title: session.title || t('history.list.untitled') })} className="ml-lg">
          {node.children.map((child) => (
            <Row
              key={child.session.id}
              nested
              node={child}
              now={now}
              parent={session}
              showFolder={showFolder}
              strips={strips}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

function Summary({ live, status, quiet }: { live: SessionSummary[]; status: StreamStatus; quiet: boolean }) {
  const { t } = useTranslation()
  const counts = stateCounts(live)
  return (
    <div className="flex flex-col gap-xs">
      {/* With nothing on the board the notice below says it; the sentence stays for screen readers. */}
      <p aria-live="polite" className={quiet ? 'sr-only' : 'text-body text-primary-ink'} role="status">
        {live.length === 0
          ? t('live.summary.none')
          : t('live.summary.live', {
              count: live.length,
              states: counts.map(([state, count]) => t(`live.summary.state.${state}`, { count })).join(' · '),
            })}
      </p>
      {status === 'retrying' ? (
        <p className="flex items-center gap-xs text-label text-secondary-ink">
          <CircleAlert aria-hidden="true" className="text-feedback-warning" size={14} strokeWidth={1.75} />
          {t('live.stream.retrying')}
        </p>
      ) : (
        <p className="text-label text-secondary-ink">{t(`live.stream.${status}`)}</p>
      )}
      <p className="text-label text-secondary-ink">
        {t('live.unknownWindow', { minutes: UNKNOWN_WINDOW_MS / 60_000 })}
      </p>
    </div>
  )
}

function GroupHeading({
  group,
  by,
  count,
  others,
  id,
}: {
  group: string | null
  by: LiveGroup
  count: number
  others: number
  id: string
}) {
  const { t } = useTranslation()
  return (
    <h2 className="flex min-w-0 items-baseline gap-sm text-label text-secondary-ink" id={id}>
      <span className="min-w-0 text-primary-ink">
        {group === null ? (
          t(`live.notRecorded.${by}`)
        ) : by === 'folder' ? (
          <Folder path={group} />
        ) : (
          t(`history.harnesses.${group}`, { defaultValue: group })
        )}
      </span>
      <span className="data shrink-0">
        {others ? t('live.sessionsWithOthers', { count, others }) : t('live.sessions', { count })}
      </span>
    </h2>
  )
}

export function LiveBoardPage() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [search, setSearch] = useSearchParams()
  const by: LiveGroup = search.get('group') === 'harness' ? 'harness' : 'folder'
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
  const arranged = useMemo(() => groups(live, by), [live, by])
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

  const changeGroup = (next: LiveGroup) => {
    const params = new URLSearchParams(search)
    if (next === 'folder') params.delete('group')
    else params.set('group', next)
    setSearch(params)
  }

  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey="workspaces.live.destinations.board.purpose" titleKey="workspaces.live.destinations.board.title" />
      <ReadingLine />
      {query.isPending ? (
        <LoadingRows label={t('live.loading')} />
      ) : query.isError ? (
        <ErrorNotice error={query.error} scope="live.error" onRetry={() => void query.refetch()} />
      ) : (
        <>
          <Summary live={live} quiet={live.length === 0 && ended.length === 0} status={status} />
          {live.length > 0 && <GroupSwitch group={by} onChange={changeGroup} />}
          {live.length > 0 && strips ? <StripsKey scale={strips.scale} /> : null}
          {live.length === 0 && ended.length === 0 && (
            <Notice body={t('live.empty.body')} title={t('live.empty.title')} />
          )}
          {arranged.map((group, index) => {
            const id = `${baseId}-group-${index}`
            return (
              <section key={String(group.key)} aria-labelledby={id} className="flex flex-col gap-sm">
                <GroupHeading by={by} count={group.count} group={group.key} id={id} others={group.others} />
                <ul className="divide-y divide-hairline overflow-hidden rounded-surface border border-hairline bg-working-paper shadow-resting">
                  {group.nodes.map((node) => (
                    <Row key={node.session.id} node={node} now={now} showFolder={by === 'harness'} strips={strips} />
                  ))}
                </ul>
              </section>
            )
          })}
          {ended.length > 0 && (
            <section aria-labelledby={`${baseId}-ended`} className="flex flex-col gap-sm">
              <div className="flex flex-col gap-xs">
                <h2 className="text-title text-primary-ink" id={`${baseId}-ended`}>
                  {t('live.ended.title')}
                </h2>
                <p className="text-body text-secondary-ink">
                  {t('live.ended.body', { minutes: ENDED_WINDOW_MS / 60_000 })}
                </p>
              </div>
              <ul className="divide-y divide-hairline overflow-hidden rounded-surface border border-hairline bg-working-paper shadow-resting">
                {ended.map((session) => (
                  <Row
                    key={session.id}
                    ended
                    node={{ session, children: [], orphan: false }}
                    now={now}
                    showFolder
                  />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </article>
  )
}
