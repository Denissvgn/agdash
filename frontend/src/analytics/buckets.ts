import type { Facets } from '../api/types'
import { datesOf, dayOf, type Filters } from '../history/filters'

/** The steps the analytics figures use, as the service names them. */
export type Bucket = 'quarter-hour' | 'hour' | 'day' | 'week'

const MINUTE = 60_000
const DAY = 86_400_000
export const STEP: Record<Bucket, number> = { 'quarter-hour': 15 * MINUTE, hour: 60 * MINUTE, day: DAY, week: 7 * DAY }

/**
 * How many days the filters span: from the first day (or the first session on
 * record) to the last (or today). Null until the facets say when the first
 * session was, if the filters do not.
 */
export function rangeDays(filters: Filters, facets: Facets | undefined, today = new Date()): number | null {
  const dates = datesOf(filters, today)
  if (!dates.from && !facets) return null
  const first = dates.from || (facets?.first_started_at ? dayOf(new Date(facets.first_started_at)) : null)
  if (!first) return 1
  const last = dates.to || dayOf(today)
  return Math.round((Date.parse(last) - Date.parse(first)) / DAY) + 1
}

/** The bucket before or after ``at``, by the local calendar for days and weeks. */
function step(at: Date, bucket: Bucket, by: 1 | -1): Date {
  if (bucket === 'hour' || bucket === 'quarter-hour') return new Date(at.getTime() + by * STEP[bucket])
  const days = bucket === 'week' ? 7 : 1
  return new Date(at.getFullYear(), at.getMonth(), at.getDate() + by * days, at.getHours(), at.getMinutes())
}

/**
 * The buckets to add before and after the service's own so the axis runs over the
 * selected dates: the service's buckets run only from the first event to the last.
 */
export function padToRange(
  starts: Date[],
  bucket: Bucket,
  range: { from: string; to: string },
  now = new Date(),
): { before: Date[]; after: Date[] } {
  const before: Date[] = []
  const after: Date[] = []
  if (!starts.length) return { before, after }
  const [fy, fm, fd] = (range.from || dayOf(starts[0])).split('-').map(Number)
  const [ty, tm, td] = (range.to || dayOf(now)).split('-').map(Number)
  // The range's first bucket: its first day, or for weeks the Monday on or before it.
  const monday = bucket === 'week' ? (new Date(fy, fm - 1, fd).getDay() + 6) % 7 : 0
  const first = new Date(fy, fm - 1, fd - monday)
  const last = starts[starts.length - 1]
  const end = new Date(Math.min(new Date(ty, tm - 1, td + 1).getTime(), Math.max(now.getTime(), last.getTime() + 1)))
  for (let at = step(starts[0], bucket, -1); at >= first; at = step(at, bucket, -1)) before.unshift(at)
  for (let at = step(last, bucket, 1); at < end; at = step(at, bucket, 1)) after.push(at)
  return { before, after }
}
