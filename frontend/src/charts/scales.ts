import { max } from 'd3-array'
import { scaleBand, scaleLinear, scaleTime } from 'd3-scale'
import type { Reported } from './data'

/** A magnitude scale from zero, rounded to readable ticks; an all-empty figure still gets one. */
export function valueScale(values: Reported[], range: [number, number]) {
  const top = max(values, (value) => value ?? 0) ?? 0
  return scaleLinear()
    .domain([0, top > 0 ? top : 1])
    .range(range)
    .nice(4)
}

/** Value ticks at readable steps, about one per 48px of axis. */
export function valueTicks(scale: ReturnType<typeof valueScale>, length: number): number[] {
  return scale.ticks(Math.max(2, Math.min(6, Math.floor(length / 48))))
}

/** One band per bucket or category; ``padding`` leaves room for the gap between bars. */
export function bandScale(keys: string[], range: [number, number], padding = 0.2) {
  return scaleBand<string>().domain(keys).range(range).paddingInner(padding).paddingOuter(padding / 2)
}

/** Bucket starts across the plot; the last bucket ends one step later. */
export function timeScale(buckets: Date[], step: number, range: [number, number]) {
  const first = buckets[0] ?? new Date(0)
  const last = buckets[buckets.length - 1] ?? first
  return scaleTime()
    .domain([first, new Date(last.getTime() + step)])
    .range(range)
}

/** Every n-th label, so labels at least ``minGap`` pixels apart never collide. */
export function labelEvery(count: number, length: number, minGap = 64): number {
  if (count <= 1) return 1
  return Math.max(1, Math.ceil(count / Math.max(1, Math.floor(length / minGap))))
}
