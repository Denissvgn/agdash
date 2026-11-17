import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pin } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { getJson, sendJson } from '../api/client'
import type { MappingEvidence, MappingRule, UnmappedItem, UnmappedQueue } from '../api/types'
import { formatWhen } from '../history/format'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows, Notice, StateChip } from '../history/SessionList'
import { reasonOf, useConnections } from './hooks'
import { ProjectSelect } from './WorkChordPage'

/** One piece of evidence, always with what it rests on. */
export function EvidenceLine({ evidence }: { evidence: MappingEvidence }) {
  const { t } = useTranslation()
  const name = evidence.name ?? t('mapping.unnamedProject', { id: evidence.target })
  return (
    <li className="text-label text-secondary-ink">
      {t('mapping.evidenceItem', { basis: t(`mapping.basis.${evidence.basis}`), name })}
    </li>
  )
}

function PinForm({ item }: { item: UnmappedItem }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const selectId = useId()
  const [project, setProject] = useState(item.suggestion ? String(item.suggestion) : '')
  const pin = useMutation({
    mutationFn: () =>
      sendJson<MappingRule>('POST', `/api/workchord/connections/${item.connection_id}/pins`, {
        session_id: item.session_id,
        project_id: Number(project),
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['mapping'] }),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (project) pin.mutate()
  }
  return (
    <form className="flex shrink-0 flex-col gap-xs" onSubmit={submit}>
      {/* The select's own first line says what to choose, so the label is for assistive technology. */}
      <label className="sr-only" htmlFor={selectId}>
        {t('mapping.pin.label')}
      </label>
      <div className="flex gap-sm">
        <div className="min-w-0 flex-1 roomy:w-[16rem] roomy:flex-none">
          <ProjectSelect
            connectionId={item.connection_id}
            id={selectId}
            placeholderKey="mapping.pin.choose"
            value={project}
            onChange={setProject}
          />
        </div>
        <button aria-busy={pin.isPending} className="button-secondary" disabled={pin.isPending} type="submit">
          <Pin aria-hidden="true" size={16} strokeWidth={1.75} />
          {pin.isPending ? t('mapping.pin.pinning') : t('mapping.pin.submit')}
        </button>
      </div>
      <p aria-live="polite" className="text-label text-secondary-ink empty:hidden" role="status">
        {pin.isError
          ? reasonOf(pin.error)
            ? t('mapping.pin.failed', { reason: reasonOf(pin.error) })
            : t('mapping.pin.failedUnknown')
          : ''}
      </p>
    </form>
  )
}

function QueueRow({ item }: { item: UnmappedItem }) {
  const { t } = useTranslation()
  const title = item.title || t('history.list.untitled')
  const harness = t(`history.harnesses.${item.harness}`, { defaultValue: item.harness })
  const when = formatWhen(item.last_activity_at)
  return (
    // What the session is on the left, what to do about it on the right: one line a session
    // where there is room, so the queue can be read down instead of scrolled through.
    <li className="flex flex-col gap-sm px-md py-sm min-[1024px]:flex-row min-[1024px]:items-start min-[1024px]:gap-lg">
      <div className="flex min-w-0 flex-1 flex-col gap-[2px]">
      <div className="flex flex-wrap items-center gap-x-sm gap-y-xs">
        <StateChip state={item.state} />
        <h3 className="min-w-[min(100%,12rem)] flex-1 break-words text-body font-semibold text-primary-ink">{title}</h3>
        <span className="data shrink-0 text-label text-secondary-ink">
          {when ? t('history.list.lastActive', { when }) : t('history.list.noActivity')}
        </span>
      </div>
      <p className="flex min-w-0 flex-wrap items-baseline gap-x-sm text-label text-secondary-ink">
        <span>{harness}</span>
        {item.cwd && (
          <>
            <span aria-hidden="true">·</span>
            <span className="data min-w-[8rem] max-w-full truncate text-left font-mono text-[0.75rem] [direction:rtl]">
              <bdi>{item.cwd}</bdi>
            </span>
          </>
        )}
      </p>
      {item.reason === 'no evidence' ? null : (
        <div className="mt-xs flex flex-col gap-xs">
          <p className="text-label text-primary-ink">{t('mapping.conflict')}</p>
          <ul aria-label={t('mapping.evidenceLabel', { title })} className="flex flex-col gap-xs">
            {item.evidence.map((evidence, index) => (
              <EvidenceLine key={`${evidence.basis}-${evidence.target}-${index}`} evidence={evidence} />
            ))}
          </ul>
        </div>
      )}
      </div>
      <PinForm item={item} />
    </li>
  )
}

export function UnmappedPage() {
  const { t } = useTranslation()
  const connections = useConnections()
  const queue = useQuery({
    queryKey: ['mapping', 'unmapped'],
    queryFn: ({ signal }) => getJson<UnmappedQueue>('/api/mapping/unmapped', { limit: 200 }, signal),
    enabled: Boolean(connections.data?.length),
  })
  const base = 'workspaces.projects.destinations.unmapped'
  const loading = connections.isPending || (Boolean(connections.data?.length) && queue.isPending)

  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${base}.purpose`} titleKey={`${base}.title`} />
      {loading ? (
        <LoadingRows label={t('mapping.loading')} />
      ) : connections.isError ? (
        <ErrorNotice error={connections.error} scope="workchord.error" onRetry={() => void connections.refetch()} />
      ) : !connections.data?.length ? (
        <Notice body={t('mapping.noServerBody')} title={t('mapping.noServerTitle')}>
          <Link className="button-secondary" to="/settings/workchord">
            {t('mapping.noServerAction')}
          </Link>
        </Notice>
      ) : queue.isError ? (
        <ErrorNotice error={queue.error} scope="workchord.error" onRetry={() => void queue.refetch()} />
      ) : !queue.data?.items.length ? (
        <Notice body={t('mapping.emptyBody')} title={t('mapping.emptyTitle')} />
      ) : (
        <>
          <p className="text-label text-secondary-ink">{t('mapping.total', { count: queue.data.total })}</p>
          <ul
            aria-label={t('mapping.queueLabel')}
            className="divide-y divide-hairline overflow-hidden rounded-surface border border-hairline bg-working-paper shadow-resting"
          >
            {queue.data.items.map((item) => (
              <QueueRow key={`${item.connection_id}-${item.session_id}`} item={item} />
            ))}
          </ul>
        </>
      )}
    </article>
  )
}
