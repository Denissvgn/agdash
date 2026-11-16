import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { getJson } from '../api/client'
import type { ActivityReport } from '../api/types'
import { Heatmap, SmallMultiples, type FigureStatus, type HeatmapData, type TimeData } from '../charts'
import { FilterBar } from '../history/FilterBar'
import { apiParams, datesOf } from '../history/filters'
import { useAddressFilters, useFacets } from '../history/hooks'
import { PageHeader } from '../history/PageHeader'
import { padToRange, rangeDays, STEP, type Bucket } from './buckets'

const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
// Monday first, as the service counts weekdays; named in English, as the catalogue is.
const weekday = new Intl.DateTimeFormat('en', { weekday: 'short' })
const WEEKDAYS = Array.from({ length: 7 }, (_, i) => weekday.format(new Date(2026, 10, 2 + i)))
const HOURS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, '0'))

/** Quarter hours for two days or less, hours up to a week, days beyond. */
function parallelBucket(days: number | null): ActivityReport['bucket'] | null {
  if (days === null) return null
  return days <= 2 ? 'quarter-hour' : days <= 7 ? 'hour' : 'day'
}

function heatData(report: ActivityReport): HeatmapData {
  const values = WEEKDAYS.map(() => HOURS.map(() => 0 as number | null))
  for (const cell of report.hour_of_week) values[cell.weekday][cell.hour] = cell.session_hours
  return {
    rows: WEEKDAYS.map((name, i) => ({ id: String(i), name })),
    columns: HOURS.map((name) => ({ id: name, name })),
    values,
  }
}

function parallelData(report: ActivityReport, range: { from: string; to: string }, name: (key: string | null) => string, total: string): TimeData {
  const starts = report.buckets.map((bucket) => new Date(bucket.start))
  const { before, after } = padToRange(starts, report.bucket as Bucket, range)
  // No session was active in the padded buckets: a true zero.
  const pad = (values: number[]) => [...before.map(() => 0), ...values, ...after.map(() => 0)]
  return {
    buckets: [...before, ...starts, ...after],
    step: STEP[report.bucket],
    series: [
      { id: '__total__', name: total, values: pad(report.total.sessions) },
      // With one harness its panel would repeat All sessions, so it is left out.
      ...(report.parallel.length > 1
        ? report.parallel.map((lane) => ({ id: lane.key ?? '__none__', name: name(lane.key), values: pad(lane.sessions) }))
        : []),
    ],
  }
}

/**
 * Analytics · Activity: when the selected sessions were active, by hour of the
 * week and as sessions at once over time, in the reader's own time zone.
 */
export function ActivityPage() {
  const { t } = useTranslation()
  const [filters, setFilters] = useAddressFilters()
  const facets = useFacets()
  const bucket = parallelBucket(rangeDays(filters, facets.data))
  const timeZone = zone()
  const report = useQuery({
    enabled: bucket !== null,
    queryKey: ['analytics-activity', bucket, filters],
    queryFn: ({ signal }) =>
      getJson<ActivityReport>('/api/analytics/activity', { ...apiParams(filters), bucket: bucket ?? 'hour', time_zone: timeZone }, signal),
    placeholderData: keepPreviousData,
  })
  const data = report.data && !report.isPlaceholderData ? report.data : undefined
  const status: FigureStatus = report.isError
    ? 'error'
    : bucket === null || (report.isFetching && !data)
      ? 'loading'
      : data && data.total.sessions.every((count) => count === 0)
        ? 'empty'
        : 'ready'
  const name = (key: string | null) =>
    key === null ? t('analytics.notRecorded') : t(`history.harnesses.${key}`, { defaultValue: key })
  const base = 'workspaces.analytics.destinations.activity'
  const step = t(`analytics.activity.steps.${bucket ?? 'hour'}`)
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${base}.purpose`} titleKey={`${base}.title`} />
      <FilterBar facets={facets.data} filters={filters} onChange={setFilters} />
      <Heatmap
        caption={t('analytics.activity.weekCaption', { zone: timeZone })}
        data={data ? heatData(data) : undefined}
        emptyBody={t('analytics.activity.emptyBody')}
        emptyTitle={t('analytics.activity.emptyTitle')}
        rowHeader={t('analytics.activity.weekday')}
        status={status}
        title={t('analytics.activity.weekTitle')}
        onRetry={() => void report.refetch()}
      />
      <SmallMultiples
        caption={t('analytics.activity.parallelCaption', { step, zone: timeZone })}
        data={data ? parallelData(data, datesOf(filters), name, t('analytics.activity.total')) : undefined}
        emptyBody={t('analytics.activity.emptyBody')}
        emptyTitle={t('analytics.activity.emptyTitle')}
        max={8}
        rowHeader={t(`analytics.activity.per.${bucket ?? 'hour'}`)}
        status={status}
        title={bucket === 'day' ? t('analytics.activity.dailyTitle') : t('analytics.activity.parallelTitle', { step })}
        variant="step"
        onRetry={() => void report.refetch()}
      />
    </article>
  )
}
