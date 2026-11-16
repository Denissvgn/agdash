import type { Coverage, Reported, TimeData } from '../charts'
import type { Facets, SeriesBucket, SeriesPoint, SeriesReport, UsageRow } from '../api/types'
import { datesOf, dayOf, type Filters } from '../history/filters'
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
 * beyond. A range with no start runs from the first session on record, so it
 * waits for the facets that say when that was (null until they arrive).
 */
export function bucketFor(filters: Filters, facets: Facets | undefined, today = new Date()): SeriesBucket | null {
  const dates = datesOf(filters, today)
  if (!dates.from && !facets) return null
  const firstOnRecord = facets?.first_started_at ? dayOf(new Date(facets.first_started_at)) : null
  const first = dates.from || firstOnRecord
  if (!first) return 'day'
  const last = dates.to || dayOf(today)
  const days = Math.round((Date.parse(last) - Date.parse(first)) / DAY) + 1
  return days <= 2 ? 'hour' : days <= 92 ? 'day' : 'week'
}

/** The bucket before or after ``at``, by the local calendar. */
function step(at: Date, bucket: SeriesBucket, by: 1 | -1): Date {
  if (bucket === 'hour') return new Date(at.getTime() + by * 3_600_000)
  const days = bucket === 'week' ? 7 : 1
  return new Date(at.getFullYear(), at.getMonth(), at.getDate() + by * days, at.getHours(), at.getMinutes())
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
  const before: Date[] = []
  const after: Date[] = []
  if (range && reported.length) {
    const [fy, fm, fd] = (range.from || dayOf(reported[0])).split('-').map(Number)
    const [ty, tm, td] = (range.to || dayOf(now)).split('-').map(Number)
    // The range's first bucket: its first day, or for weeks the Monday on or before it.
    const monday = report.bucket === 'week' ? (new Date(fy, fm - 1, fd).getDay() + 6) % 7 : 0
    const first = new Date(fy, fm - 1, fd - monday)
    const end = new Date(Math.min(new Date(ty, tm - 1, td + 1).getTime(), Math.max(now.getTime(), reported[reported.length - 1].getTime() + 1)))
    for (let at = step(reported[0], report.bucket, -1); at >= first; at = step(at, report.bucket, -1)) {
      before.unshift(at)
    }
    for (let at = step(reported[reported.length - 1], report.bucket, 1); at < end; at = step(at, report.bucket, 1)) {
      after.push(at)
    }
  }
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
