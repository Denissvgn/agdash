import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { getJson } from '../api/client'
import type { Page, SessionSummary } from '../api/types'
import { useSessionName } from '../history/names'
import { StateChip } from '../history/SessionList'
import { endedAt, lastActive, type Node } from './board'
import { formatAgo } from './format'
import { Folder, type LinkTo, type Strips } from './parts'
import { RowStrips } from './Strips'

interface LatestPrompt {
  kind: string
  payload: { text?: string }
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-[2px]">
      <dt className="text-label text-secondary-ink">{label}</dt>
      <dd className="min-w-0 break-words text-body text-primary-ink">{children}</dd>
    </div>
  )
}

function Prompt({ sessionId }: { sessionId: number }) {
  const { t } = useTranslation()
  const { data, isPending } = useQuery({
    queryKey: ['live', 'prompt', sessionId],
    queryFn: ({ signal }) =>
      getJson<Page<LatestPrompt>>(`/api/sessions/${sessionId}/events`, { kind: 'user_prompt', order: 'desc', limit: 1 }, signal),
    staleTime: Infinity,
  })
  const text = data?.items[0]?.payload.text?.trim()
  if (isPending) {
    // A still placeholder holds the line, so the panel does not jump when the prompt arrives.
    return <span aria-hidden="true" className="inline-block h-[10px] w-2/5 rounded-compact bg-canvas-mist align-middle" />
  }
  return text ? <span className="line-clamp-6">{text}</span> : <span className="text-secondary-ink">{t('live.row.noPrompt')}</span>
}

/**
 * Everything the board knows about one session, beside the rows: what a slim row has
 * no room for. It follows the row picked, and "Open session" leads to the session's page.
 */
export function SessionPanel({
  node,
  parent,
  now,
  strips,
  ended,
  to,
}: {
  node: Node
  /** The session this one was started by, when it is on the board. */
  parent?: SessionSummary
  now: number
  strips?: Strips
  ended: boolean
  to: LinkTo
}) {
  const { t } = useTranslation()
  const { session } = node
  const name = useSessionName()
  const harnessOf = (of: SessionSummary) => t(`history.harnesses.${of.harness}`, { defaultValue: of.harness })
  const surface = session.surface ? t(`history.surfaces.${session.surface}`) : null
  const basis = session.state_basis ? t(`live.basis.${session.state_basis}`, { defaultValue: '' }) : ''
  const when = ended ? endedAt(session) : lastActive(session)
  const row = ended ? undefined : strips?.rows.get(session.id)
  const how = t(`live.parentBasis.${session.parent_basis ?? 'unknown'}`, { defaultValue: t('live.parentBasis.unknown') })
  return (
    <aside
      aria-label={t('live.panel.label')}
      className="flex flex-col gap-md rounded-surface border border-hairline bg-working-paper p-md shadow-resting min-[1024px]:sticky min-[1024px]:top-md"
      data-panel={session.id}
    >
      <Link className="flex items-center gap-xs text-label text-secondary-ink hover:underline pointer-coarse:min-h-touch min-[1024px]:hidden" data-panel-back="" replace to={to(null)}>
        <ArrowLeft aria-hidden="true" size={14} strokeWidth={1.75} />
        {t('live.panel.back')}
      </Link>
      <div className="flex flex-col gap-xs">
        {/* Takes focus where the panel replaces the rows, so a pick does not leave focus on nothing. */}
        <h2 className="break-words text-title text-primary-ink focus:outline-none" data-panel-title="" tabIndex={-1}>
          {name(session)}
        </h2>
        <p className="flex flex-wrap items-center gap-sm text-label text-secondary-ink">
          <StateChip state={session.state} />
          {when
            ? t(ended ? 'live.row.ended' : 'live.row.lastActive', { when: formatAgo(when, now) })
            : t('history.list.noActivity')}
        </p>
      </div>
      <dl className="flex flex-col gap-md">
        {basis ? <Fact label={t('live.panel.why')}>{basis}</Fact> : null}
        <Fact label={t('live.row.latestPrompt')}>
          <Prompt sessionId={session.id} />
        </Fact>
        {row && strips ? (
          <Fact label={t('live.panel.window')}>
            <RowStrips stacked from={strips.from} row={row} scale={strips.scale} until={strips.until} />
          </Fact>
        ) : null}
        {parent ? (
          <Fact label={t('live.panel.startedBy')}>
            <Link className="underline underline-offset-2" replace to={to(parent.id)}>
              {name(parent)}
            </Link>
            <span className="block text-label text-secondary-ink" data-edge={session.parent_basis ?? 'unknown'}>
              {parent.harness !== session.harness
                ? t('live.row.edge', { harness: harnessOf(parent), basis: how })
                : t('live.panel.knownFrom', { basis: how })}
            </span>
          </Fact>
        ) : node.orphan ? (
          <Fact label={t('live.panel.startedBy')}>{t('live.panel.parentNotLive')}</Fact>
        ) : null}
        {node.children.length > 0 ? (
          <Fact label={t('live.panel.started', { count: node.children.length })}>
            <ul className="flex flex-col gap-[2px]">
              {node.children.map((child) => (
                <li key={child.session.id} className="flex min-w-0 items-start gap-sm">
                  <StateChip state={child.session.state} />
                  <Link className="min-w-0 break-words underline underline-offset-2" replace to={to(child.session.id)}>
                    {name(child.session)}
                  </Link>
                </li>
              ))}
            </ul>
          </Fact>
        ) : null}
        <Fact label={t('live.panel.harness')}>
          {[surface ? t('history.list.harnessSurface', { harness: harnessOf(session), surface }) : harnessOf(session),
            session.models.at(-1) ?? t('history.list.noModel'),
            session.effort ? t('live.row.effort', { effort: session.effort }) : null]
            .filter(Boolean)
            .join(' · ')}
        </Fact>
        {session.cwd ? (
          <Fact label={t('live.panel.folder')}>
            <Folder path={session.cwd} />
          </Fact>
        ) : null}
      </dl>
      <Link className="button-secondary justify-center" to={`/history/sessions/${session.id}`}>
        {t('live.panel.open')}
        <ArrowRight aria-hidden="true" size={16} strokeWidth={1.75} />
      </Link>
    </aside>
  )
}
