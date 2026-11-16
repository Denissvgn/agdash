import { scaleLinear } from 'd3-scale'
import type { Ink } from './ink'

/** The heatmap's steps: the whole ink ramp, lightest first. */
export const HEAT_STEPS: Ink[] = ['ink-1', 'ink-2', 'ink-3', 'ink-4', 'ink-5', 'ink-6', 'ink-7']

/**
 * The ink step for a value: a reported zero is the lightest step, and the rest of
 * the range is cut into six equal bins up to the largest value.
 */
export function heatStep(value: number, max: number): Ink {
  if (value <= 0 || max <= 0) return 'ink-1'
  return HEAT_STEPS[Math.min(6, Math.max(1, Math.ceil((value / max) * 6)))]
}

/** The upper bound of each step, for the scale legend. */
export function heatBounds(max: number): number[] {
  return [0, ...Array.from({ length: 6 }, (_, i) => (max * (i + 1)) / 6)]
}

/** The largest value rounded up to a readable bound that six bins divide evenly. */
export function niceTop(max: number): number {
  if (max <= 0) return 0
  const top = scaleLinear().domain([0, max]).nice(6).domain()[1]
  return top % 6 === 0 || top < 6 ? top : Math.ceil(top / 6) * 6
}
