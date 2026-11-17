import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { getJson } from '../api/client'
import type { ProjectActivity, ProjectGroup } from '../api/types'
import { Dumbbell, Lanes, type FigureStatus, type Lane } from '../charts'
import { formatValue } from '../charts/format'
import { RANGES, type Range } from './ranges'

const HOUR = 3_600_000
/**
 * Idle stretches longer than this are compressed: a working day's gaps in a week. A month
 * stays on one linear axis, since a break for every night would leave no room to read it.
 */
const THRESHOLD: Record<Range, number> = { '7': 2 * HOUR, '30': Number.POSITIVE_INFINITY }

/** The last 7 or 30 days as pills over native radios; the choice lives in the address. */
export function RangeSwitch({ value, onChange }: { value: Range; onChange: (range: Range) => void }) {
  const { t } = useTranslation()
  const name = useId()
  return (
    <fieldset className="flex flex-wrap items-center gap-sm">
      <legend className="float-left mr-sm text-label text-secondary-ink">{t('board.activity.range')}</legend>
      {RANGES.map((option) => (
        <label
          key={option}
          className="toggle-chip relative cursor-pointer has-[:checked]:border-planning-thread has-[:checked]:bg-planning-thread-soft has-[:checked]:text-planning-thread has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-planning-thread"
        >
          <input checked={value === option} className="sr-only" name={name} type="radio" value={option} onChange={() => onChange(option)} />
          {t('history.filters.lastDays', { count: Number(option) })}
        </label>
      ))}
    </fieldset>
  )
}

/** Each project's sessions over the last 7 or 30 days as lanes, with the now line. */
export function ProjectActivityFigure({ connection, range }: { connection: string | null; range: Range }) {
  const { t } = useTranslation()
  const activity = useQuery({
    queryKey: ['projects', 'activity', connection, range],
    queryFn: ({ signal }) => getJson<ProjectActivity>('/api/projects/activity', { days: range, connection_id: connection }, signal),
    placeholderData: keepPreviousData,
  })
  const data = activity.data && !activity.isPlaceholderData ? activity.data : undefined
  const status: FigureStatus = activity.isError ? 'error' : activity.isFetching && !data ? 'loading' : 'ready'
  const lanes: Lane[] | undefined = data?.lanes.map((lane) => ({
    id: String(lane.project?.id ?? 'unmapped'),
    name: lane.project?.name ?? t('board.unmapped'),
    items: lane.sessions.flatMap((session) => {
      if (!session.start) return []
      const start = new Date(session.start)
      const end = new Date(session.end ?? session.start)
      return [{ id: String(session.id), label: session.title || t('history.list.untitled'), start, end: end > start ? end : undefined }]
    }),
  }))
  return (
    <Lanes
      caption={range === '7' ? t('board.activity.caption', { hours: THRESHOLD[range] / HOUR }) : t('board.activity.captionLinear')}
      emptyBody={t('board.activity.emptyBody', { count: Number(range) })}
      emptyTitle={t('board.activity.emptyTitle')}
      lanes={lanes}
      now={data ? new Date(data.until) : undefined}
      rowHeader={t('board.activity.project')}
      status={status}
      threshold={THRESHOLD[range]}
      tickSpacing={range === '30' ? 200 : undefined}
      title={t('board.activity.title', { count: Number(range) })}
      onRetry={() => void activity.refetch()}
    />
  )
}

/** Per agent, what was declared (current assignments) against what was observed (live sessions mapped to it). */
export function AgentsFigure({ group }: { group: ProjectGroup }) {
  const { t } = useTranslation()
  const name = group.project?.name ?? t('board.unmapped')
  const rows = group.agents.map((agent) => {
    const bases = Object.entries(agent.observed_bases ?? {})
      .sort((a, b) => b[1] - a[1])
      .map(([basis, count]) => t('board.agents.basis', { count, basis: t(`mapping.basis.${basis}`, { defaultValue: basis }) }))
    return {
      id: String(agent.id),
      name: agent.name,
      declared: agent.assignments ?? (agent.assigned ? 1 : 0),
      observed: agent.live_sessions,
      detail: bases.length ? bases.join(t('board.agents.and')) : undefined,
    }
  })
  return (
    <Dumbbell
      caption={t('board.agents.caption')}
      declaredLabel={t('board.agents.declared')}
      detailLabel={t('board.agents.how')}
      observedLabel={t('board.agents.observed')}
      rowHeader={t('board.agents.agent')}
      rows={rows}
      title={t('board.agents.title', { project: name })}
      valueText={(row) => t('board.agents.values', { declared: formatValue(row.declared), observed: formatValue(row.observed) })}
    />
  )
}
