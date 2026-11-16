import { useQuery } from '@tanstack/react-query'
import { TriangleAlert } from 'lucide-react'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import { getJson } from '../api/client'
import type { AttentionFlag, BoardCard, FlagCode, ProjectBoard, ProjectGroup } from '../api/types'
import { formatWhen } from '../history/format'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows, Notice, StateChip } from '../history/SessionList'

const REFRESH_MS = 15_000

function number(value: number | null | undefined): string | null {
  return value === null || value === undefined ? null : value.toLocaleString('en')
}

function FlagChip({ code }: { code: FlagCode }) {
  const { t } = useTranslation()
  return (
    <span className="inline-flex h-[22px] items-center gap-xs rounded-full border border-feedback-warning bg-working-paper px-sm text-label text-feedback-warning">
      <TriangleAlert aria-hidden="true" size={13} strokeWidth={1.75} />
      {t(`board.flags.${code}.short`)}
    </span>
  )
}

function flagText(flag: AttentionFlag, t: (key: string, options?: Record<string, unknown>) => string): string {
  const task = flag.task_key ?? (flag.task_id !== null ? `#${flag.task_id}` : t('board.unknownTask'))
  const when = formatWhen(flag.since)
  return t(`board.flags.${flag.code}.long`, {
    task,
    run: flag.run_id,
    actor: flag.actor ?? t('board.unknownAgent'),
    observed: flag.observed,
    bound: flag.bound,
    when: when ?? '',
  })
}

function Attention({ flags, titles }: { flags: AttentionFlag[]; titles: Map<number, string> }) {
  const { t } = useTranslation()
  const titleId = useId()
  if (flags.length === 0) return null
  return (
    <section
      aria-labelledby={titleId}
      className="flex flex-col gap-sm rounded-surface border border-feedback-warning bg-working-paper p-[20px] shadow-resting"
    >
      <h2 className="flex items-center gap-sm text-title text-primary-ink" id={titleId}>
        <TriangleAlert aria-hidden="true" className="text-feedback-warning" size={18} strokeWidth={1.75} />
        {t('board.attention', { count: flags.length })}
      </h2>
      <p className="max-w-[70ch] text-label text-secondary-ink">{t('board.attentionPurpose')}</p>
      <ul className="flex flex-col gap-xs">
        {flags.map((flag, index) => (
          <li key={`${flag.code}-${flag.session_id}-${flag.run_id}-${index}`} className="text-body text-primary-ink">
            {t('board.flagLine', { flag: t(`board.flags.${flag.code}.short`), text: flagText(flag, t) })}
            {flag.session_id !== null && (
              <>
                {' '}
                <Link className="text-secondary-ink underline underline-offset-2" to={`/history/sessions/${flag.session_id}`}>
                  {titles.get(flag.session_id) ?? t('history.list.untitled')}
                </Link>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Usage({ card }: { card: BoardCard }) {
  const { t } = useTranslation()
  const declared = card.run?.declared_usage?.quantities
  const observedIn = number(card.observed_usage.input_tokens)
  const observedOut = number(card.observed_usage.output_tokens)
  if (!card.run && !card.observed_usage.messages) return null
  const none = t('board.notReported')
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-md gap-y-[2px] text-label">
      <dt className="text-secondary-ink">{t('board.declared')}</dt>
      <dd className="data text-primary-ink">
        {declared
          ? t('board.tokens', {
              input: number(declared.input_tokens) ?? none,
              output: number(declared.output_tokens) ?? none,
            })
          : none}
      </dd>
      <dt className="text-secondary-ink">{t('board.observed')}</dt>
      <dd className="data text-primary-ink">
        {card.observed_usage.messages ? t('board.tokens', { input: observedIn ?? none, output: observedOut ?? none }) : none}
      </dd>
    </dl>
  )
}

function SessionCard({ card, nested = false }: { card: BoardCard; nested?: boolean }) {
  const { t } = useTranslation()
  const title = card.title || t('history.list.untitled')
  const harness = t(`history.harnesses.${card.harness}`, { defaultValue: card.harness })
  const claim = formatWhen(card.task?.claim_expires_at ?? null)
  const last = formatWhen(card.last_activity_at)
  return (
    <li className={`flex flex-col gap-sm ${nested ? 'border-l-2 border-hairline pl-md' : 'px-md py-[12px]'}`}>
      <div className="flex flex-wrap items-center gap-x-sm gap-y-xs">
        <StateChip state={card.state} />
        <h4 className="min-w-[min(100%,12rem)] flex-1 break-words text-body font-semibold text-primary-ink">
          <Link className="hover:underline focus-visible:underline" to={`/history/sessions/${card.id}`}>
            {title}
          </Link>
        </h4>
        {card.flags.map((code) => (
          <FlagChip key={code} code={code} />
        ))}
      </div>
      <p className="flex flex-wrap gap-x-sm text-label text-secondary-ink">
        <span>{harness}</span>
        {card.actor && (
          <>
            <span aria-hidden="true">·</span>
            <span>
              {t('board.agent', { name: card.actor.name, basis: card.actor.basis ? t(`mapping.basis.${card.actor.basis}`) : '' })}
            </span>
          </>
        )}
        {card.project_basis && (
          <>
            <span aria-hidden="true">·</span>
            <span>{t('board.mappedBy', { basis: t(`mapping.basis.${card.project_basis}`) })}</span>
          </>
        )}
        {last && (
          <>
            <span aria-hidden="true">·</span>
            <span className="data">{t('history.list.lastActive', { when: last })}</span>
          </>
        )}
      </p>
      {card.task && (
        <p className="flex flex-wrap gap-x-sm text-label text-secondary-ink">
          <span className="font-semibold text-primary-ink">
            {card.task.key ? t('board.taskWithKey', { key: card.task.key, title: card.task.title }) : card.task.title}
          </span>
          {card.task.status && <span>{t('board.status', { status: card.task.status })}</span>}
          {card.task.iteration && <span>{card.task.iteration}</span>}
          {card.work && <span>{t(`board.work.${card.work}`)}</span>}
          {claim && <span className="data">{t('board.claimUntil', { when: claim })}</span>}
        </p>
      )}
      {(card.run || card.observed_models.length > 0) && (
        <p className="flex flex-wrap gap-x-sm text-label text-secondary-ink">
          {card.run && <span>{t('board.run', { id: card.run.id, status: card.run.status ?? t('board.notReported') })}</span>}
          <span>
            {t('board.models', {
              bound: card.run?.bound_model ?? t('board.notBound'),
              observed: card.observed_models.length ? card.observed_models.join(', ') : t('board.notReported'),
            })}
          </span>
        </p>
      )}
      <Usage card={card} />
      {card.subagents.length > 0 && (
        <ul aria-label={t('board.subagents', { title })} className="flex flex-col gap-sm">
          {card.subagents.map((sub) => (
            <SessionCard key={sub.id} card={sub} nested />
          ))}
        </ul>
      )}
    </li>
  )
}

function Group({ group }: { group: ProjectGroup }) {
  const { t } = useTranslation()
  const titleId = useId()
  const name = group.project?.name ?? t('board.unmapped')
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-sm">
        <h2 className="text-title text-primary-ink" id={titleId}>
          {name}
        </h2>
        <span className="data text-label text-secondary-ink">{t('board.sessions', { count: group.sessions.length })}</span>
      </div>
      {group.reason === 'unmapped' ? (
        <p className="text-label text-secondary-ink">
          {t('board.unmappedHelp')}{' '}
          <Link className="underline underline-offset-2" to="/projects/unmapped">
            {t('board.unmappedLink')}
          </Link>
        </p>
      ) : (
        group.agents.length > 0 && (
          <ul aria-label={t('board.agentsLabel', { project: name })} className="flex flex-wrap gap-xs">
            {group.agents.map((agent) => (
              <li
                key={agent.id}
                className="inline-flex h-[24px] items-center rounded-full border border-hairline bg-working-paper px-sm text-label text-secondary-ink"
              >
                {agent.assigned
                  ? t('board.agentAssigned', { name: agent.name, count: agent.live_sessions })
                  : t('board.agentLive', { name: agent.name, count: agent.live_sessions })}
              </li>
            ))}
          </ul>
        )
      )}
      <ul
        aria-label={t('board.sessionsLabel', { project: name })}
        className="divide-y divide-hairline overflow-hidden rounded-surface border border-hairline bg-working-paper shadow-resting"
      >
        {group.sessions.map((card) => (
          <SessionCard key={card.id} card={card} />
        ))}
      </ul>
    </section>
  )
}

function titlesOf(groups: ProjectGroup[]): Map<number, string> {
  const found = new Map<number, string>()
  const visit = (card: BoardCard) => {
    if (card.title) found.set(card.id, card.title)
    card.subagents.forEach(visit)
  }
  groups.forEach((group) => group.sessions.forEach(visit))
  return found
}

export function ProjectBoardPage() {
  const { t } = useTranslation()
  const [search, setSearch] = useSearchParams()
  const selectId = useId()
  const chosen = search.get('connection')
  const board = useQuery({
    queryKey: ['projects', 'board', chosen],
    queryFn: ({ signal }) => getJson<ProjectBoard>('/api/projects/board', { connection_id: chosen }, signal),
    refetchInterval: REFRESH_MS,
  })
  const base = 'workspaces.projects.destinations.board'
  const data = board.data
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${base}.purpose`} titleKey={`${base}.title`} />
      {data && data.connections.length > 1 && (
        <div className="flex max-w-[24rem] flex-col gap-xs">
          <label className="text-label text-secondary-ink" htmlFor={selectId}>
            {t('board.server')}
          </label>
          <select
            className="field"
            id={selectId}
            value={String(data.connection?.id ?? '')}
            onChange={(event) => {
              const next = new URLSearchParams(search)
              next.set('connection', event.target.value)
              setSearch(next)
            }}
          >
            {data.connections.map((connection) => (
              <option key={connection.id} value={String(connection.id)}>
                {connection.name ?? connection.server}
              </option>
            ))}
          </select>
        </div>
      )}
      {board.isPending ? (
        <LoadingRows label={t('board.loading')} />
      ) : board.isError ? (
        <ErrorNotice error={board.error} scope="workchord.error" onRetry={() => void board.refetch()} />
      ) : (
        <>
          {!data!.connection && (
            <Notice body={t('mapping.noServerBody')} title={t('mapping.noServerTitle')}>
              <Link className="button-secondary" to="/settings/workchord">
                {t('mapping.noServerAction')}
              </Link>
            </Notice>
          )}
          <Attention flags={data!.flags} titles={titlesOf(data!.groups)} />
          {data!.groups.length === 0 ? (
            <Notice body={t('board.emptyBody')} title={t('board.emptyTitle')} />
          ) : (
            data!.groups.map((group) => <Group key={group.project?.id ?? 'unmapped'} group={group} />)
          )}
        </>
      )}
    </article>
  )
}
