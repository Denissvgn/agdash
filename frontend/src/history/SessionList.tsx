import { CircleAlert, CircleDashed } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { Excerpt, SessionHit, SessionState, SessionSummary } from '../api/types'
import { formatWhen } from './format'

const STATE_DOT: Record<SessionState, string> = {
  active: 'bg-status-active',
  waiting: 'bg-status-planned',
  idle: 'bg-status-planned',
  ended: 'bg-status-closed',
  unknown: 'bg-status-planned',
}

/** A session's state as WorkChord's canonical status colours show it, always with its word. */
export function StateChip({ state }: { state: SessionState }) {
  const { t } = useTranslation()
  return (
    <span className="inline-flex h-[22px] shrink-0 items-center gap-xs rounded-full border border-hairline bg-working-paper px-sm text-label text-secondary-ink">
      <span aria-hidden="true" className={`size-[6px] rounded-full ${STATE_DOT[state] ?? STATE_DOT.unknown}`} />
      {t(`history.states.${state}`)}
    </span>
  )
}

export function ExcerptText({ excerpt }: { excerpt: Excerpt }) {
  const { t } = useTranslation()
  return (
    <p className="max-w-[90ch] text-body text-secondary-ink">
      <span className="mr-sm text-label text-secondary-ink">{t(`history.kinds.${excerpt.kind}`)}</span>
      <span className="break-words text-primary-ink">
        {excerpt.snippet.map((part, index) =>
          part.match ? (
            <mark key={index} className="rounded-compact bg-planning-thread-soft px-[1px] font-semibold text-primary-ink">
              {part.text}
            </mark>
          ) : (
            <span key={index}>{part.text}</span>
          ),
        )}
      </span>
    </p>
  )
}

function Row({ session }: { session: SessionSummary | SessionHit }) {
  const { t } = useTranslation()
  const harness = t(`history.harnesses.${session.harness}`, { defaultValue: session.harness })
  const surface = session.surface ? t(`history.surfaces.${session.surface}`) : null
  const lastActive = formatWhen(session.last_activity_at ?? session.started_at)
  const hit = 'excerpts' in session ? session : null
  return (
    <li className="flex flex-col gap-xs px-md py-[12px]">
      <div className="flex flex-wrap items-center gap-x-sm gap-y-xs">
        <StateChip state={session.state} />
        {/* At least 12rem for the title; the date wraps below it rather than squeezing it. */}
        <h3 className="line-clamp-2 min-w-[min(100%,12rem)] flex-1 break-words text-body font-semibold text-primary-ink roomy:line-clamp-none roomy:truncate">
          <Link className="hover:underline focus-visible:underline" to={`/history/sessions/${session.id}`}>
            {session.title || t('history.list.untitled')}
          </Link>
        </h3>
        <span className="data shrink-0 text-label text-secondary-ink">
          {lastActive ? t('history.list.lastActive', { when: lastActive }) : t('history.list.noActivity')}
        </span>
      </div>
      <p className="flex flex-wrap gap-x-sm text-label text-secondary-ink">
        <span>{surface ? t('history.list.harnessSurface', { harness, surface }) : harness}</span>
        <span aria-hidden="true">·</span>
        <span className="min-w-0 truncate">
          {session.models.length ? session.models.join(', ') : t('history.list.noModel')}
        </span>
        <span aria-hidden="true">·</span>
        <span className="data">{t('history.list.events', { count: session.event_count })}</span>
        {session.child_count > 0 && (
          <>
            <span aria-hidden="true">·</span>
            <span className="data">{t('history.list.subagents', { count: session.child_count })}</span>
          </>
        )}
        {hit && (
          <>
            <span aria-hidden="true">·</span>
            <span className="data">{t('history.search.hits', { count: hit.hits })}</span>
          </>
        )}
      </p>
      {/* Cut from the start: the last folders are what tell sessions apart. */}
      {session.cwd && (
        <p className="data truncate text-left font-mono text-[0.75rem] text-secondary-ink [direction:rtl]">
          <bdi>{session.cwd}</bdi>
        </p>
      )}
      {hit && hit.excerpts.length > 0 && (
        <div aria-label={t('history.search.excerpts')} className="mt-xs flex flex-col gap-xs" role="group">
          {hit.excerpts.map((excerpt) => (
            <ExcerptText key={excerpt.event_id} excerpt={excerpt} />
          ))}
        </div>
      )}
    </li>
  )
}

export function SessionRows({ sessions, label }: { sessions: (SessionSummary | SessionHit)[]; label: string }) {
  return (
    <ul
      aria-label={label}
      className="divide-y divide-hairline overflow-hidden rounded-surface border border-hairline bg-working-paper shadow-resting"
    >
      {sessions.map((session) => (
        <Row key={session.id} session={session} />
      ))}
    </ul>
  )
}

/** Placeholder rows while the first page loads: still shapes, no motion and no numbers. */
export function LoadingRows({ label }: { label: string }) {
  return (
    <div
      aria-busy="true"
      aria-label={label}
      className="divide-y divide-hairline rounded-surface border border-hairline bg-working-paper"
      role="status"
    >
      {[0, 1, 2, 3].map((row) => (
        <div key={row} className="flex flex-col gap-sm px-md py-[14px]">
          <div className="h-[12px] w-2/5 rounded-compact bg-canvas-mist" />
          <div className="h-[10px] w-3/5 rounded-compact bg-canvas-mist" />
        </div>
      ))}
    </div>
  )
}

/** A quiet panel for empty and error states: what is true, and what to do next. */
export function Notice({
  title,
  body,
  tone = 'quiet',
  children,
}: {
  title: string
  body: string
  tone?: 'quiet' | 'problem'
  children?: ReactNode
}) {
  const Icon = tone === 'problem' ? CircleAlert : CircleDashed
  return (
    <section
      className="flex flex-col gap-sm rounded-surface border border-hairline bg-working-paper p-[20px] shadow-resting"
    >
      <h2 className="flex items-center gap-sm text-title text-primary-ink">
        <Icon
          aria-hidden="true"
          className={tone === 'problem' ? 'text-feedback-warning' : 'text-status-planned'}
          size={18}
          strokeWidth={1.75}
        />
        {title}
      </h2>
      <p className="max-w-[70ch] text-body text-secondary-ink">{body}</p>
      {children && <div className="mt-xs flex flex-wrap gap-sm">{children}</div>}
    </section>
  )
}

/** ``scope`` names the catalogue entry whose copy fits the page, such as "live.error". */
export function ErrorNotice({
  error,
  onRetry,
  scope = 'history.error',
}: {
  error: unknown
  onRetry: () => void
  scope?: string
}) {
  const { t } = useTranslation()
  const kind = error instanceof ApiError ? error.kind : 'server'
  const key = kind === 'unreachable' ? 'unreachable' : kind === 'refused' ? 'refused' : 'server'
  return (
    <Notice body={t(`${scope}.${key}Body`)} title={t(`${scope}.${key}Title`)} tone="problem">
      <button className="button-secondary" type="button" onClick={onRetry}>
        {t(`${scope}.retry`)}
      </button>
    </Notice>
  )
}

export function ShowMore({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex justify-center">
      <button aria-busy={busy} className="button-secondary" disabled={busy} type="button" onClick={onClick}>
        {busy ? t('history.list.loadingMore') : t('history.list.more')}
      </button>
    </div>
  )
}
