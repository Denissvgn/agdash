/**
 * The shapes figures draw from. A value is a number the harness reported, or null
 * when it did not: null is drawn hatched and labelled, never as zero.
 */

export type Reported = number | null

/** One named quantity, such as a harness's sessions or a tool's calls. */
export type Category = { id: string; name: string; value: Reported }

/** Values over shared time buckets, one series per entity, aligned bucket for bucket. */
export type TimeSeries = { id: string; name: string; values: Reported[] }
export type TimeData = {
  /** Each bucket's start. */
  buckets: Date[]
  /** The bucket width, in milliseconds. */
  step: number
  series: TimeSeries[]
}

/** The ink steps that tell up to three series in one figure apart, with a legend and labels. */
export const SERIES_INK = ['ink-7', 'ink-5', 'ink-3'] as const
export const MAX_SERIES = SERIES_INK.length
/** Ranked bars show this many rows, the rest folded into Other. */
export const MAX_BARS = 12
export const OTHER = '__other__'

const sum = (values: Reported[]): Reported => {
  const reported = values.filter((value): value is number => value !== null)
  return reported.length ? reported.reduce((a, b) => a + b, 0) : null
}

/** Ranked, largest first, with not-reported last; past ``max`` the tail folds into Other. */
export function rankCategories(categories: Category[], max: number, otherName: string): Category[] {
  const ranked = [...categories].sort((a, b) => (b.value ?? -1) - (a.value ?? -1))
  if (ranked.length <= max) return ranked
  const kept = ranked.slice(0, max - 1)
  return [...kept, { id: OTHER, name: otherName, value: sum(ranked.slice(max - 1).map((item) => item.value)) }]
}

/** At most ``max`` series; past it the rest fold into one Other series, bucket for bucket. */
export function foldSeries(series: TimeSeries[], max: number, otherName: string): TimeSeries[] {
  if (series.length <= max) return series
  const rest = series.slice(max - 1)
  const values = rest[0].values.map((_, i) => sum(rest.map((item) => item.values[i])))
  return [...series.slice(0, max - 1), { id: OTHER, name: otherName, values }]
}

/** Whether any value in the figure was not reported, so its legend explains the hatch. */
export function hasUnreported(values: Reported[]): boolean {
  return values.some((value) => value === null)
}

/** Runs of consecutive unreported buckets, as [first, last] index pairs. */
export function unreportedRuns(values: Reported[]): [number, number][] {
  const runs: [number, number][] = []
  values.forEach((value, i) => {
    if (value !== null) return
    const last = runs[runs.length - 1]
    if (last && last[1] === i - 1) last[1] = i
    else runs.push([i, i])
  })
  return runs
}
