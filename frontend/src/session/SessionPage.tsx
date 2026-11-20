import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { getJson } from '../api/client'
import type { EventOut, Page, SessionDetail, SessionSummary, ThoughtsCoverage } from '../api/types'
import { formatWhen } from '../history/format'
import { ErrorNotice, LoadingRows, Notice, StateChip } from '../history/SessionList'
import { useDocumentTitle } from '../shell/useDocumentTitle'
import { ExportPanel, ExportToggle } from './ExportControl'
import { MappingFigure } from './MappingFigure'
import { messageEncrypted } from './messages'
import { SessionOverview } from './Overview'
import { VirtualList, type VirtualListHandle } from './VirtualList'
import { useSessionName } from '../history/names'

const PAGE = 500
const PREVIEW = 1200
const ROW_ESTIMATE = 76
const clock = new Intl.DateTimeFormat('en', { timeStyle: 'medium' })

function text(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** How much of the model's reasoning the harness kept, always in words. */
export function CoverageBadge({ coverage }: { coverage: ThoughtsCoverage | null }) {
  const { t } = useTranslation()
  const level = coverage ?? 'unknown'
  return (
    <span className="inline-flex h-[20px] items-center rounded-full border border-hairline bg-canvas-mist px-sm text-label text-secondary-ink">
      {t(`session.coverage.${level}`)}
    </span>
  )
}

function Body({ value, mono, expanded, onToggle }: { value: string; mono?: boolean; expanded: boolean; onToggle: () => void }) {
  const { t } = useTranslation()
  if (!value) return null
  const long = value.length > PREVIEW
  const shown = long && !expanded ? `${value.slice(0, PREVIEW)}…` : value
  return (
    <div className="flex flex-col items-start gap-xs">
      <p
        className={`max-w-[90ch] whitespace-pre-wrap break-words text-primary-ink ${mono ? 'font-mono text-[0.8125rem]' : 'text-body'}`}
      >
        {shown}
      </p>
      {long && (
        <button aria-expanded={expanded} className="button-ghost" type="button" onClick={onToggle}>
          {expanded ? t('session.showLess') : t('session.showAll', { count: value.length })}
        </button>
      )}
    </div>
  )
}

function EventRow({ event, expanded, onToggle }: { event: EventOut; expanded: boolean; onToggle: () => void }) {
  const { t } = useTranslation()
  const p = event.payload
  const when = event.occurred_at ? clock.format(new Date(event.occurred_at)) : null
  const name = typeof p.name === 'string' ? p.name : null
  let heading = t(`history.kinds.${event.kind}`)
  let body = ''
  let mono = false
  let badge = null
  switch (event.kind) {
    case 'user_prompt':
      body = text(p.text)
      if (messageEncrypted(p))
        badge = (
          <span className="inline-flex h-[20px] items-center rounded-full border border-hairline bg-canvas-mist px-sm text-label text-secondary-ink">
            {t('session.messageEncrypted')}
          </span>
        )
      break
    case 'assistant_text':
      body = text(p.text)
      break
    case 'thinking':
      body = text(p.text)
      badge = <CoverageBadge coverage={event.thoughts_coverage} />
      break
    case 'tool_call':
      heading = name ? t('session.toolCall', { name }) : heading
      body = text(p.input)
      mono = true
      break
    case 'tool_result':
      heading = name ? t('session.toolResult', { name }) : heading
      body = text(p.output)
      mono = true
      if (p.is_error === true)
        badge = (
          <span className="inline-flex h-[20px] items-center rounded-full border border-feedback-warning px-sm text-label text-primary-ink">
            {t('session.error')}
          </span>
        )
      break
    case 'subagent':
      heading = p.phase === 'finished' ? t('session.subagentFinished') : t('session.subagentStarted')
      body = [p.agent_type, p.description, p.prompt, p.result].filter((part) => typeof part === 'string' && part).join('\n\n')
      break
    case 'model_change':
      body = p.previous_model
        ? t('session.modelChangedFrom', { model: text(p.model), previous: text(p.previous_model) })
        : t('session.modelChanged', { model: text(p.model) })
      break
    case 'compaction':
      body = text(p.summary)
      break
    case 'status':
      body = [p.status, p.detail].filter((part) => typeof part === 'string' && part).join(' — ')
      break
    case 'usage':
      body = t('session.usage', {
        input: p.input_tokens ?? t('session.notReported'),
        output: p.output_tokens ?? t('session.notReported'),
      })
      break
  }
  const quiet = ['status', 'usage', 'model_change'].includes(event.kind)
  return (
    <div className={`flex gap-md border-b border-hairline px-md ${quiet ? 'py-xs' : 'py-[12px]'}`}>
      <span className="data w-[6.5rem] shrink-0 pt-[2px] text-label text-secondary-ink">{when ?? t('session.noTime')}</span>
      <div className="flex min-w-0 flex-1 flex-col gap-xs">
        <p className="flex flex-wrap items-center gap-sm">
          <span className={`text-label ${quiet ? 'text-secondary-ink' : 'font-semibold text-primary-ink'}`}>{heading}</span>
          {badge}
        </p>
        <Body expanded={expanded} mono={mono} value={body} onToggle={onToggle} />
      </div>
    </div>
  )
}

/** How many of the sessions a session started are named in its header; the rest are counted. */
const NAMED = 8

/**
 * The sessions around this one, by name: the one that started it, and those it started.
 * A session can start hundreds, so only the most recently active are named.
 */
function Family({ session }: { session: SessionDetail }) {
  const { t } = useTranslation()
  const name = useSessionName()
  const parentId = session.parent_session_id
  const parent = useQuery({
    queryKey: ['session', parentId],
    queryFn: ({ signal }) => getJson<SessionDetail>(`/api/sessions/${parentId}`, {}, signal),
    enabled: parentId !== null,
  })
  const started = useQuery({
    queryKey: ['session', session.id, 'started'],
    queryFn: ({ signal }) => getJson<Page<SessionSummary>>('/api/sessions', { parent: session.id, limit: NAMED }, signal),
    enabled: session.children.length > 0,
  })
  if (parentId === null && session.children.length === 0) return null
  // Until their names arrive, and if they do not, the links are still there by number.
  const children = started.data?.items.length
    ? started.data.items.map((child) => ({ id: child.id, label: name(child) }))
    : session.children.slice(0, NAMED).map((id, index) => ({ id, label: t('session.subagent', { number: index + 1 }) }))
  const more = session.children.length - children.length
  return (
    <nav aria-label={t('session.family')} className="flex flex-col gap-xs text-label">
      {parentId !== null && (
        <p className="flex flex-wrap items-baseline gap-x-sm">
          <span className="text-secondary-ink">{t('session.startedBy')}</span>
          <Link className="text-primary-ink underline underline-offset-2" to={`/history/sessions/${parentId}`}>
            {parent.data ? name(parent.data) : t('session.parent')}
          </Link>
        </p>
      )}
      {children.length > 0 && (
        <p className="flex flex-wrap items-baseline gap-x-sm gap-y-[2px]">
          <span className="text-secondary-ink">{t('session.children', { count: session.children.length })}</span>
          {children.map((child) => (
            <Link key={child.id} className="text-primary-ink underline underline-offset-2" to={`/history/sessions/${child.id}`}>
              {child.label}
            </Link>
          ))}
          {more > 0 && <span className="text-secondary-ink">{t('session.moreChildren', { count: more })}</span>}
        </p>
      )}
    </nav>
  )
}

function Header({ session }: { session: SessionDetail }) {
  const { t } = useTranslation()
  const name = useSessionName()
  const title = name(session)
  useDocumentTitle(title)
  const harness = t(`history.harnesses.${session.harness}`, { defaultValue: session.harness })
  const started = formatWhen(session.started_at)
  const last = formatWhen(session.last_activity_at)
  const [exporting, setExporting] = useState(false)
  const exportId = useId()
  return (
    <header className="flex flex-col gap-sm">
      <p>
        <Link className="text-label text-secondary-ink underline-offset-2 hover:underline" to="/history">
          {t('session.back')}
        </Link>
      </p>
      <div className="flex flex-wrap items-center gap-sm">
        <StateChip state={session.state} />
        <h1 className="min-w-0 break-words text-display text-primary-ink" id="page-title">
          {title}
        </h1>
        <ExportToggle controls={exportId} open={exporting} onToggle={() => setExporting(!exporting)} />
      </div>
      <ExportPanel id={exportId} open={exporting} sessionId={session.id} />
      <p className="flex flex-wrap gap-x-sm text-label text-secondary-ink">
        <span>{harness}</span>
        {session.models.length > 0 && (
          <>
            <span aria-hidden="true">·</span>
            <span>{session.models.join(', ')}</span>
          </>
        )}
        {started && (
          <>
            <span aria-hidden="true">·</span>
            <span className="data">{t('session.started', { when: started })}</span>
          </>
        )}
        {last && (
          <>
            <span aria-hidden="true">·</span>
            <span className="data">{t('history.list.lastActive', { when: last })}</span>
          </>
        )}
      </p>
      {session.cwd && (
        <p className="data truncate text-left font-mono text-[0.75rem] text-secondary-ink [direction:rtl]">
          <bdi>{session.cwd}</bdi>
        </p>
      )}
      <Family session={session} />
    </header>
  )
}

function Timeline({ session }: { session: SessionDetail }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set())
  const events = useInfiniteQuery({
    queryKey: ['session-events', session.id],
    queryFn: ({ pageParam, signal }) =>
      getJson<Page<EventOut>>(`/api/sessions/${session.id}/events`, { cursor: pageParam, limit: PAGE }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })
  const items = useMemo(() => events.data?.pages.flatMap((page) => page.items) ?? [], [events.data])
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = events
  const more = useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])
  // The overview's window follows the rows in view; moving it scrolls the list there,
  // loading further pages until an event at that time is loaded.
  const list = useRef<VirtualListHandle>(null)
  const [range, setRange] = useState<[number, number] | null>(null)
  // A time to scroll to once an event at it is loaded.
  const pending = useRef<number | null>(null)
  const onRange = useCallback((first: number, last: number) => setRange([first, last]), [])
  const timeOf = useCallback(
    (index: number) => {
      // An undated event takes the time of the nearest dated one before it, or after it.
      for (let i = index; i >= 0; i -= 1) if (items[i]?.occurred_at) return Date.parse(items[i].occurred_at!)
      for (let i = index + 1; i < items.length; i += 1) if (items[i]?.occurred_at) return Date.parse(items[i].occurred_at!)
      return null
    },
    [items],
  )
  const view = useMemo((): [number, number] | null => {
    if (!range) return null
    const from = timeOf(range[0])
    const to = timeOf(range[1])
    return from === null || to === null ? null : [from, Math.max(from, to)]
  }, [range, timeOf])
  const seek = useCallback(
    (at: number) => {
      const index = items.findIndex((_, i) => (timeOf(i) ?? -Infinity) >= at)
      if (index >= 0) {
        pending.current = null
        list.current?.scrollToIndex(index)
      } else if (hasNextPage) {
        pending.current = at
        if (!isFetchingNextPage) void fetchNextPage()
      } else {
        pending.current = null
        list.current?.scrollToIndex(items.length - 1)
      }
    },
    [items, timeOf, hasNextPage, isFetchingNextPage, fetchNextPage],
  )
  // Each page that arrives is searched again for the time the reader moved to.
  useEffect(() => {
    if (pending.current !== null && !isFetchingNextPage) seek(pending.current)
  }, [items, isFetchingNextPage, seek])
  const toggle = (id: number) =>
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  if (events.isPending) return <LoadingRows label={t('session.loading')} />
  if (events.isError) return <ErrorNotice error={events.error} onRetry={() => void events.refetch()} />
  if (items.length === 0) return <Notice body={t('session.emptyBody')} title={t('session.emptyTitle')} />
  return (
    <section aria-labelledby="timeline-title" className="flex flex-col gap-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-sm">
        <h2 className="text-title text-primary-ink" id="timeline-title">
          {t('session.timeline')}
        </h2>
        <p aria-live="polite" className="data text-label text-secondary-ink">
          {hasNextPage
            ? t('session.loaded', { loaded: items.length, total: session.event_count })
            : t('session.all', { count: items.length })}
        </p>
      </div>
      <SessionOverview session={session} view={view} onSeek={seek} />
      <VirtualList
        busy={isFetchingNextPage}
        handle={list}
        className="h-[70vh] min-h-[24rem] rounded-surface border border-hairline bg-working-paper shadow-resting"
        count={items.length}
        estimate={ROW_ESTIMATE}
        label={t('session.timelineLabel')}
        renderRow={(index) => (
          <EventRow
            event={items[index]}
            expanded={expanded.has(items[index].id)}
            onToggle={() => toggle(items[index].id)}
          />
        )}
        total={Math.max(session.event_count, items.length)}
        onNearEnd={more}
        onRange={onRange}
      />
    </section>
  )
}

export function SessionPage() {
  const { t } = useTranslation()
  const { sessionId } = useParams()
  const id = Number(sessionId)
  const valid = Number.isInteger(id) && id > 0
  const session = useQuery({
    queryKey: ['session', id],
    queryFn: ({ signal }) => getJson<SessionDetail>(`/api/sessions/${id}`, {}, signal),
    enabled: valid,
  })
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      {!valid ? (
        <>
          <h1 className="text-display text-primary-ink" id="page-title">
            {t('session.missingTitle')}
          </h1>
          <Notice body={t('session.missingBody')} title={t('session.missingTitle')} />
        </>
      ) : session.isPending ? (
        <>
          <h1 className="sr-only" id="page-title">
            {t('session.loadingTitle')}
          </h1>
          <LoadingRows label={t('session.loadingTitle')} />
        </>
      ) : session.isError ? (
        <>
          <h1 className="text-display text-primary-ink" id="page-title">
            {t('session.missingTitle')}
          </h1>
          <ErrorNotice error={session.error} onRetry={() => void session.refetch()} />
        </>
      ) : (
        <>
          <Header session={session.data} />
          <MappingFigure session={session.data} />
          <Timeline session={session.data} />
        </>
      )}
    </article>
  )
}
