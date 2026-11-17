import { ChevronRight } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { SessionSummary } from '../api/types'
import { useSessionName } from '../history/names'
import { StateChip } from '../history/SessionList'
import { endedAt, flatten, isNew, lastActive, type Group, type LiveGroup, type Node } from './board'
import { formatAgo } from './format'
import { Folder, type LinkTo, type Strips } from './parts'
import { Lane } from './Strips'
import { useRowKeys, useShortAgo, useStripWords } from './words'

// One set of columns for the header and every row, so the lanes share a clock:
// state, title, harness (where there is room), the last 30 minutes, last active.
export const COLUMNS =
  'grid items-center gap-x-sm px-md grid-cols-[5.5rem_minmax(0,1fr)_2.5rem] roomy:grid-cols-[5.5rem_minmax(0,1fr)_208px_4.25rem] min-[1340px]:grid-cols-[5.5rem_minmax(0,1fr)_6.5rem_208px_4.25rem]'

/** The names of the columns and the lanes' clock. Rows say all of it in words, so this is for the eye. */
export function RosterHead() {
  const { t } = useTranslation()
  return (
    <div aria-hidden="true" className={`${COLUMNS} h-[28px] border-b border-hairline text-counter text-secondary-ink`} data-roster-head="">
      <span>{t('live.columns.state')}</span>
      <span>{t('live.columns.session')}</span>
      <span className="hidden min-[1340px]:block">{t('live.columns.harness')}</span>
      <span className="hidden justify-between roomy:flex">
        <span>{t('live.columns.from')}</span>
        <span>{t('live.columns.now')}</span>
      </span>
      <span className="truncate text-right">
        <span className="hidden roomy:inline">{t('live.columns.active')}</span>
      </span>
    </div>
  )
}

/** Marks a session that started within the last minute, where it was added: nothing moves to show it. */
export function NewTag() {
  const { t } = useTranslation()
  return (
    <span className="ml-sm shrink-0 rounded-compact border border-planning-thread px-xs text-counter text-planning-thread" data-new="">
      {t('live.row.new')}
    </span>
  )
}

function Row({
  session,
  depth,
  now,
  strips,
  selected,
  dense,
  ended,
  to,
}: {
  session: SessionSummary
  depth: number
  now: number
  strips?: Strips
  selected: boolean
  dense: boolean
  ended: boolean
  to: LinkTo
}) {
  const { t } = useTranslation()
  const ago = useShortAgo()
  const words = useStripWords()
  const onKeyDown = useRowKeys()
  const name = useSessionName()
  const id = useId()
  const harness = t(`history.harnesses.${session.harness}`, { defaultValue: session.harness })
  const when = ended ? endedAt(session) : lastActive(session)
  const row = ended ? undefined : strips?.rows.get(session.id)
  return (
    <li
      className={`${COLUMNS} relative ${dense ? 'h-[24px]' : 'h-[28px]'} text-label pointer-coarse:h-touch hover:bg-canvas-mist data-[selected=true]:bg-planning-thread-soft data-[selected=true]:before:absolute data-[selected=true]:before:inset-y-0 data-[selected=true]:before:left-0 data-[selected=true]:before:w-[2px] data-[selected=true]:before:bg-planning-thread`}
      data-selected={selected}
      data-session={session.id}
    >
      <span className="flex" id={`${id}-state`}>
        <StateChip state={session.state} />
      </span>
      <span className="flex min-w-0 items-center" style={{ paddingLeft: depth * 16 }}>
        {depth > 0 ? (
          <span aria-hidden="true" className="mr-xs h-[8px] w-[8px] shrink-0 -translate-y-[3px] rounded-bl-compact border-b border-l border-secondary-ink" />
        ) : null}
        {/* The link covers the row, so the whole row selects the session. */}
        <Link
          aria-current={selected ? 'true' : undefined}
          // The link's name is the title alone; its state and times come after, as its description.
          aria-describedby={`${id}-state ${id}-words`}
          className="truncate font-semibold text-primary-ink after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:outline focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-planning-thread"
          data-row=""
          replace
          to={to(session.id)}
          onKeyDown={onKeyDown}
        >
          {name(session)}
        </Link>
        {!ended && isNew(session, now) ? <NewTag /> : null}
      </span>
      <span className="hidden truncate text-secondary-ink min-[1340px]:block">{harness}</span>
      <span aria-hidden="true" className="hidden roomy:block">
        {row && strips ? (
          <Lane from={strips.from} row={row} scale={strips.scale} until={strips.until} />
        ) : ended ? null : (
          // Nothing recorded in the window: an empty baseline, so the row does not look unfinished.
          <span className="block h-px w-[208px] translate-y-[8px] bg-hairline" data-lane-empty="" />
        )}
      </span>
      <span aria-hidden="true" className="data text-right text-secondary-ink">
        {when ? ago(when, now) : '–'}
      </span>
      <span className="sr-only" id={`${id}-words`}>
        {when
          ? t(ended ? 'live.row.ended' : 'live.row.lastActive', { when: formatAgo(when, now) })
          : t('history.list.noActivity')}
        {row ? ` ${words(row)}` : ''}
      </span>
    </li>
  )
}

export function GroupHeading({ group, by, count, others, id }: { group: string | null; by: LiveGroup; count: number; others: number; id: string }) {
  const { t } = useTranslation()
  return (
    <h2 className="flex h-[28px] min-w-0 items-center gap-sm bg-canvas-mist px-md text-label text-secondary-ink" id={id}>
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

function Rows({
  nodes,
  label,
  ...shared
}: {
  nodes: Node[]
  label?: string
  now: number
  strips?: Strips
  selected: number | null
  dense: boolean
  ended?: boolean
  to: LinkTo
}) {
  return (
    <ul aria-label={label} className="divide-y divide-hairline">
      {flatten(nodes).map(({ node, depth }) => (
        <Row
          key={node.session.id}
          dense={shared.dense}
          depth={depth}
          ended={shared.ended ?? false}
          now={shared.now}
          selected={shared.selected === node.session.id}
          session={node.session}
          strips={shared.strips}
          to={shared.to}
        />
      ))}
    </ul>
  )
}

/**
 * The board as slim rows on one clock: a row a session, its last 30 minutes in a lane
 * that lines up with every other row's, sessions it started indented under it.
 */
export function Roster({
  groups,
  by,
  baseId,
  quiet,
  ended,
  children,
  ...shared
}: {
  groups: Group[]
  by: LiveGroup
  baseId: string
  /** Trees only left open, folded into one line. */
  quiet: Node[]
  ended: SessionSummary[]
  now: number
  strips?: Strips
  selected: number | null
  dense: boolean
  to: LinkTo
  /** Drawn in place of the grouped rows: the agents view. */
  children?: ReactNode
}) {
  const { t } = useTranslation()
  const quietCount = flatten(quiet).length
  return (
    <div
      className="overflow-hidden rounded-surface border border-hairline bg-working-paper shadow-resting"
      data-roster=""
    >
      {children ?? (
        <>
          <RosterHead />
          {groups.map((group, index) => {
            const id = `${baseId}-group-${index}`
            return (
              <section key={String(group.key)} aria-labelledby={id} className="border-b border-hairline last:border-b-0">
                <GroupHeading by={by} count={group.count} group={group.key} id={id} others={group.others} />
                <Rows nodes={group.nodes} {...shared} />
              </section>
            )
          })}
        </>
      )}
      {quietCount > 0 && (
        <details className="group border-t border-hairline first:border-t-0" data-quiet="">
          <summary className="flex h-[28px] cursor-pointer list-none items-center gap-xs bg-canvas-mist px-md text-label text-primary-ink pointer-coarse:h-touch [&::-webkit-details-marker]:hidden">
            <ChevronRight aria-hidden="true" className="shrink-0 text-secondary-ink group-open:rotate-90" size={14} strokeWidth={1.75} />
            {t('live.quiet.summary', { count: quietCount })}
          </summary>
          <Rows label={t('live.quiet.summary', { count: quietCount })} nodes={quiet} {...shared} />
        </details>
      )}
      {ended.length > 0 && (
        <section aria-labelledby={`${baseId}-ended`} className="border-t border-hairline first:border-t-0">
          <h2 className="flex h-[28px] items-center gap-sm bg-canvas-mist px-md text-label text-primary-ink" id={`${baseId}-ended`}>
            {t('live.ended.title')}
            <span className="data text-secondary-ink">{t('live.sessions', { count: ended.length })}</span>
          </h2>
          <Rows ended nodes={ended.map((session) => ({ session, children: [], orphan: false }))} {...shared} />
        </section>
      )}
    </div>
  )
}
