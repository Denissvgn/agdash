import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { getJson } from '../api/client'
import type { StartedReport } from '../api/types'
import { SelectionWindow, type FigureStatus, type Range, type TimeData } from '../charts'
import { apiParams, datesOf, dayOf, type Filters } from './filters'

const DAY = 86_400_000
const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const local = (day: string) => {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date)
}

/** Every day from the first session's start to today, with the sessions started on each. */
function daysOf(report: StartedReport, today: Date): { day: string; sessions: number }[] {
  const days = [...report.days]
  if (!days.length) return days
  for (let at = local(days[days.length - 1].day); ; ) {
    at = new Date(at.getFullYear(), at.getMonth(), at.getDate() + 1)
    if (at > today) break
    days.push({ day: dayOf(at), sessions: 0 })
  }
  return days
}

/** The picked days as bucket indices; with no dates, every day. */
function rangeOf(days: { day: string }[], filters: Filters): Range {
  const { from, to } = datesOf(filters)
  const last = days.length - 1
  const index = (day: string, fallback: number) => {
    if (!day) return fallback
    const found = days.findIndex((item) => item.day >= day)
    return found < 0 ? last : found
  }
  const start = index(from, 0)
  const end = to ? Math.max(start, days.reduce((found, item, i) => (item.day <= to ? i : found), -1)) : last
  return [start, Math.max(start, end)]
}

/**
 * Sessions started per day above the session list, for the current filters apart
 * from the dates. Picking a range in it sets "Started between", so the date fields
 * and the list follow; picking every day clears the dates.
 */
export function Overview({ filters, onChange }: { filters: Filters; onChange: (filters: Filters) => void }) {
  const { t } = useTranslation()
  // Every filter but the dates, which this figure picks.
  const scope: Filters = { ...filters, from: '', to: '', range: '' }
  const report = useQuery({
    queryKey: ['history-started', scope],
    queryFn: ({ signal }) =>
      getJson<StartedReport>('/api/analytics/started', { ...apiParams(scope), time_zone: zone() }, signal),
    placeholderData: keepPreviousData,
  })
  const today = new Date()
  const days = report.data ? daysOf(report.data, today) : []
  const data: TimeData | undefined = report.data
    ? {
        buckets: days.map((item) => local(item.day)),
        step: DAY,
        series: [{ id: 'started', name: t('history.overview.series'), values: days.map((item) => item.sessions) }],
      }
    : undefined
  const status: FigureStatus = report.isError ? 'error' : report.isPending ? 'loading' : 'ready'
  const pick = ([start, end]: Range) => {
    const everything = start === 0 && end === days.length - 1
    onChange(
      everything
        ? { ...filters, from: '', to: '', range: '' }
        : { ...filters, from: days[start].day, to: days[end].day, range: '' },
    )
  }
  return (
    <SelectionWindow
      caption={t('history.overview.caption')}
      data={data}
      emptyBody={t('history.overview.emptyBody')}
      emptyTitle={t('history.overview.emptyTitle')}
      height={120}
      rowHeader={t('history.overview.day')}
      status={status}
      title={t('history.overview.title')}
      value={days.length ? rangeOf(days, filters) : [0, 0]}
      onChange={pick}
      whole={(from, to) => t('history.overview.whole', { from, to })}
      onRetry={() => void report.refetch()}
    />
  )
}
