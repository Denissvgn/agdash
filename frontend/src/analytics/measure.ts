import type { Coverage, Reported, TimeData } from '../charts'
import type { Facets, SeriesBucket, SeriesPoint, SeriesReport, UsageRow } from '../api/types'
import type { Filters } from '../history/filters'
import { padToRange, rangeDays } from './buckets'
import { formatCost, formatCount } from './format'

/** What the usage figure and the ledger's share bars measure; the choice lives in the address. */
export const MEASURES = ['tokens', 'cost', 'sessions'] as const
export type Measure = (typeof MEASURES)[number]

const TOKEN_COUNTS = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens'] as const

type Numbers = Pick<SeriesPoint, (typeof TOKEN_COUNTS)[number] | 'reported_cost'> & { sessions: number | null }

/** A point's or row's value for a measure; tokens add up what was reported, and nothing reported is null. */
export function measureOf(item: Numbers, measure: Measure): Reported {
  if (measure === 'cost') return item.reported_cost
  if (measure === 'sessions') return item.sessions
  const reported = TOKEN_COUNTS.map((key) => item[key]).filter((value): value is number => value !== null)
  return reported.length ? reported.reduce((a, b) => a + b, 0) : null
}

/**
 * A bucket's value. A bucket where no session was active holds a true zero; one
 * where sessions were active but none reported the number stays null.
 */
export function bucketValue(point: SeriesPoint, measure: Measure): Reported {
  const value = measureOf(point, measure)
  if (value !== null) return value
  const idle = point.sessions === 0 || (point.sessions === null && point.messages === 0)
  return idle && measure !== 'sessions' ? 0 : value
}

/** How many of a bucket's active sessions reported the measure, where that is known. */
export function coverageOf(point: SeriesPoint, measure: Measure): Coverage | null {
  if (measure === 'sessions' || point.sessions === null || point.sessions === 0) return null
  const count = measure === 'cost' ? (point.reported_by.reported_cost ?? 0) : point.sessions_with_usage
  return { count: Math.min(count, point.sessions), total: point.sessions }
}

const DAY = 86_400_000
export const STEPS: Record<SeriesBucket, number> = { hour: 3_600_000, day: DAY, week: 7 * DAY }

/**
 * Hours for a range of two days or less, days up to about three months, weeks
 * beyond; null until the facets say when the first session was, if it matters.
 */
export function bucketFor(filters: Filters, facets: Facets | undefined, today = new Date()): SeriesBucket | null {
  const days = rangeDays(filters, facets, today)
  if (days === null) return null
  return days <= 2 ? 'hour' : days <= 92 ? 'day' : 'week'
}

/**
 * The report as figure data: one series per harness, model or folder, largest
 * first by the measure, with the unrecorded key last. The service's buckets run
 * from the first event to the last; with a date range they are extended to its
 * first and last day, as true zeros, since no session in the selection was active
 * there.
 */
export function figureData(
  report: SeriesReport,
  measure: Measure,
  name: (key: string | null) => string,
  range?: { from: string; to: string },
  now = new Date(),
): TimeData {
  const ranked = [...report.series].sort((a, b) => {
    if (a.key === null || b.key === null) return a.key === null ? 1 : -1
    return (measureOf(b.total, measure) ?? -1) - (measureOf(a.total, measure) ?? -1)
  })
  const reported = report.buckets.map((bucket) => new Date(bucket.start))
  const { before, after } = range ? padToRange(reported, report.bucket, range, now) : { before: [], after: [] }
  const pad = <T,>(values: T[], fill: T): T[] => [...before.map(() => fill), ...values, ...after.map(() => fill)]
  return {
    buckets: [...before, ...reported, ...after],
    step: STEPS[report.bucket],
    series: ranked.map((line) => ({
      id: line.key ?? '__none__',
      name: name(line.key),
      values: pad(
        line.points.map((point) => bucketValue(point, measure)),
        measure === 'sessions' && line.points[0]?.sessions === null ? null : 0,
      ),
      coverage: pad(
        line.points.map((point) => coverageOf(point, measure)),
        null,
      ),
    })),
  }
}

/** The ledger row's value for the share bar beside it, and for its order. */
export const rowValue = (row: UsageRow, measure: Measure): Reported => measureOf(row, measure)

/** How a measure's numbers read: cost in dollars, the rest as whole counts. */
export const formatMeasure = (measure: Measure) => (measure === 'cost' ? formatCost : formatCount)

/** Ledger rows by the measure, largest first; rows with nothing reported, then the unrecorded key, last. */
export function byMeasure(rows: UsageRow[], measure: Measure): UsageRow[] {
  return [...rows].sort((a, b) => {
    if (a.key === null || b.key === null) return a.key === null ? (b.key === null ? 0 : 1) : -1
    return (rowValue(b, measure) ?? -1) - (rowValue(a, measure) ?? -1)
  })
}

/** How many sessions reported the measure, out of all in the selection, from the ledger's total. */
export function ledgerCoverage(total: UsageRow, measure: Measure): Coverage | null {
  if (measure === 'sessions') return null
  const count = measure === 'cost' ? (total.reported_by?.reported_cost ?? 0) : total.sessions_with_usage
  return { count, total: total.sessions }
}
