import type { Reported, TimeData } from './data'
import { formatCompact, formatTick, formatValue, formatWhen } from './format'
import { labelWidth } from './geometry'
import type { Mark } from './Plot'
import { labelEvery, valueScale, valueTicks } from './scales'

export const TOP = 8
export const AXIS = 22

/** The frame of a time figure: where the plot sits, its value scale and its bucket slots. */
export function timeFrame(data: TimeData, totals: Reported[], width: number, height: number, right = 8) {
  const bottom = height - AXIS
  const probe = valueScale(totals, [bottom, TOP])
  const ticks = valueTicks(probe, bottom - TOP)
  const left = Math.max(...ticks.map((tick) => labelWidth(formatCompact(tick))), 28)
  const plotRight = Math.max(left + 1, width - right)
  const y = valueScale(totals, [bottom, TOP])
  const slot = (plotRight - left) / Math.max(1, data.buckets.length)
  const start = (i: number) => left + i * slot
  const centre = (i: number) => start(i) + slot / 2
  const every = labelEvery(data.buckets.length, plotRight - left, 72)
  const labels = data.buckets
    .map((at, i) => ({ key: at.toISOString(), x: centre(i), text: formatTick(at, data.step), i }))
    .filter((label) => label.i % every === 0)
  return { left, right: plotRight, top: TOP, bottom, y, ticks, slot, start, centre, labels }
}

/** Per bucket, the sum of what was reported, or null when nothing was. */
export function bucketTotals(data: TimeData): Reported[] {
  return data.buckets.map((_, i) => {
    const reported = data.series.map((series) => series.values[i]).filter((value): value is number => value !== null)
    return reported.length ? reported.reduce((a, b) => a + b, 0) : null
  })
}

/** The table twin of a time figure: one row per bucket, one column per series. */
export function timeTable(data: TimeData, rowHeader: string, format: (value: number) => string = formatValue) {
  return {
    rowHeader,
    columns: data.series.map((series) => ({ key: series.id, label: series.name })),
    rows: data.buckets.map((at, i) => ({
      key: at.toISOString(),
      header: formatWhen(at, data.step),
      cells: data.series.map((series) => {
        const value = series.values[i]
        return value === null ? null : format(value)
      }),
    })),
  }
}

/** One mark per bucket, carrying every series' value at it. */
export function bucketMarks(
  data: TimeData,
  x: (i: number) => number,
  y: (i: number) => number,
  describe: (value: Reported) => string,
): Mark[] {
  return data.buckets.map((at, i) => {
    const name = formatWhen(at, data.step)
    if (data.series.length === 1) return { x: x(i), y: y(i), name, value: describe(data.series[0].values[i]) }
    return {
      x: x(i),
      y: y(i),
      name,
      value: '',
      rows: data.series.map((series) => ({ name: series.name, value: describe(series.values[i]) })),
    }
  })
}
