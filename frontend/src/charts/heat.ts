import { scaleLinear } from 'd3-scale'
import type { Ink } from './ink'

/** The heatmap's steps: the whole ink ramp, lightest first. */
export const HEAT_STEPS: Ink[] = ['ink-1', 'ink-2', 'ink-3', 'ink-4', 'ink-5', 'ink-6', 'ink-7']

/** The steps a heatmap uses and each one's upper bound, for its cells and its scale legend. */
export type HeatScale = { steps: Ink[]; bounds: number[] }

/** The largest value rounded up to a readable bound that six bins divide evenly. */
export function niceTop(max: number): number {
  if (max <= 0) return 0
  const top = scaleLinear().domain([0, max]).nice(6).domain()[1]
  return top % 6 === 0 || top < 6 ? top : Math.ceil(top / 6) * 6
}

/**
 * A reported zero takes the lightest step. Whole counts up to six get a step each,
 * spread over the ramp and ending at the strongest; anything else is cut into six
 * equal bins up to a rounded top.
 */
export function heatScale(values: number[]): HeatScale {
  const max = Math.max(0, ...values)
  if (max <= 0) return { steps: ['ink-1'], bounds: [0] }
  if (values.every(Number.isInteger) && max <= 6) {
    const picks = Array.from({ length: max }, (_, i) => HEAT_STEPS[7 - max + i])
    return { steps: ['ink-1', ...picks], bounds: Array.from({ length: max + 1 }, (_, i) => i) }
  }
  const top = niceTop(max)
  return { steps: HEAT_STEPS, bounds: [0, ...Array.from({ length: 6 }, (_, i) => (top * (i + 1)) / 6)] }
}

/** The step for a value: the first whose upper bound holds it. */
export function heatStep(value: number, scale: HeatScale): Ink {
  const index = scale.bounds.findIndex((bound) => value <= bound)
  return scale.steps[index < 0 ? scale.steps.length - 1 : index]
}
