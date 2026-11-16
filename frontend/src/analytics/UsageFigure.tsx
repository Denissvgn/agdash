import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { getJson } from '../api/client'
import type { Facets, SeriesReport, UsageGroup, UsageRow } from '../api/types'
import { SmallMultiples, type FigureStatus } from '../charts'
import { apiParams, datesOf, type Filters } from '../history/filters'
import { formatCostCompact } from './format'
import { bucketFor, figureData, formatMeasure, ledgerCoverage, measureOf, type Measure } from './measure'

/** Every harness has a panel; models and folders show the top six and fold the rest into Other. */
const PANELS = 7

const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

/**
 * Usage over time above the ledger: one panel per harness, model or folder, on one
 * shared scale, with columns per hour, day or week by the date range. It asks for
 * the same sessions as the ledger, so its total is the ledger's total.
 */
export function UsageFigure({
  filters,
  group,
  measure,
  facets,
  ledger,
}: {
  filters: Filters
  group: UsageGroup
  measure: Measure
  facets: Facets | undefined
  /** The ledger's total row, which the caption's coverage quotes, so the two say the same. */
  ledger: UsageRow
}) {
  const { t } = useTranslation()
  const bucket = bucketFor(filters, facets)
  const report = useQuery({
    enabled: bucket !== null,
    queryKey: ['analytics-series', group, bucket, filters],
    queryFn: ({ signal }) =>
      getJson<SeriesReport>('/api/analytics/series', { ...apiParams(filters), group, bucket, time_zone: zone() }, signal),
    placeholderData: keepPreviousData,
  })
  const name = (key: string | null) => {
    if (key === null) return t('analytics.notRecorded')
    if (group === 'harness') return t(`history.harnesses.${key}`, { defaultValue: key })
    // A folder is named by its last part; the ledger below gives the whole path.
    return group === 'folder' ? (key.split('/').filter(Boolean).pop() ?? key) : key
  }
  const data = report.data
  const format = formatMeasure(measure)
  const unsplit = group === 'model' && measure === 'sessions'
  const status: FigureStatus = report.isError
    ? 'error'
    : unsplit
      ? 'empty'
      : report.isFetching && (!data || report.isPlaceholderData)
        ? 'loading'
        : 'ready'
  const shown = data && !report.isPlaceholderData ? data : undefined
  const total = data ? measureOf(data.total.total, measure) : null
  const coverage = ledgerCoverage(ledger, measure)
  const measureName = t(`analytics.measures.${measure}`)

  const caption = !data
    ? undefined
    : [
        total === null ? t('analytics.figure.noneReported') : t('analytics.figure.total', { value: format(total) }),
        coverage && coverage.count < coverage.total
          ? t('analytics.figure.coverage', { count: coverage.count, total: coverage.total })
          : null,
        data.undated_usage > 0 ? t('analytics.figure.undated', { count: data.undated_usage }) : null,
      ]
        .filter(Boolean)
        .join(' ')

  return (
    <SmallMultiples
      caption={unsplit ? undefined : caption}
      data={shown ? figureData(shown, measure, name, datesOf(filters)) : undefined}
      emptyBody={unsplit ? t('analytics.figure.unsplitBody') : undefined}
      emptyTitle={unsplit ? t('analytics.figure.unsplitTitle') : undefined}
      formatAxis={measure === 'cost' ? formatCostCompact : undefined}
      formatValue={format}
      max={PANELS}
      rowHeader={t(`analytics.figure.per.${bucket ?? 'day'}`)}
      status={status}
      title={t('analytics.figure.title', {
        measure: measureName,
        bucket: t(`analytics.figure.buckets.${bucket ?? 'day'}`),
        group: t(`analytics.figure.by.${group}`),
      })}
      variant="columns"
      onRetry={() => void report.refetch()}
    />
  )
}
