/**
 * The figure layer's mark geometry (DESIGN.md, Figures: Furniture and marks), as
 * numbers and SVG paths, so tests can hold the drawn shapes to the rules.
 */

/** A bar or column is at most this thick. */
export const BAR_MAX = 24
/** The rounded corner at a bar's data end; its base on the baseline stays square. */
export const DATA_END = 4
/** Touching fills are parted by this much of the surface colour, never by a stroke. */
export const GAP = 2
/** Lines, with round joins. */
export const LINE = 2
/** Dots are at least this wide, with a ring of the surface colour around them. */
export const DOT = 8
export const RING = 2
/** Gridlines and the crosshair. */
export const HAIRLINE = 1
/** An area is its line's colour at this opacity. */
export const AREA_OPACITY = 0.1
/** The not-reported hatch: 1px lines at 45°, this far apart, at this opacity. */
export const HATCH_SPACING = 6
export const HATCH_OPACITY = 0.35

const round = (value: number) => Math.round(value * 100) / 100

/** The thickness of one bar in a band, leaving at least the gap between neighbours. */
export function barThickness(band: number): number {
  return Math.max(1, Math.min(BAR_MAX, band - GAP))
}

/** The data-end radius for a bar of this thickness and length. */
export function endRadius(thickness: number, length: number): number {
  return Math.max(0, Math.min(DATA_END, thickness / 2, length))
}

/**
 * A horizontal bar from the baseline at ``x0`` to its value at ``x1`` (x1 ≥ x0), top
 * edge at ``y``: square at the base, rounded at the data end.
 */
export function horizontalBar(x0: number, x1: number, y: number, thickness: number, rounded = true): string {
  const length = Math.max(0, x1 - x0)
  const r = rounded ? endRadius(thickness, length) : 0
  const bottom = y + thickness
  if (r === 0) return `M${round(x0)},${round(y)}H${round(x0 + length)}V${round(bottom)}H${round(x0)}Z`
  const end = x0 + length
  return [
    `M${round(x0)},${round(y)}`,
    `H${round(end - r)}`,
    `A${r},${r} 0 0 1 ${round(end)},${round(y + r)}`,
    `V${round(bottom - r)}`,
    `A${r},${r} 0 0 1 ${round(end - r)},${round(bottom)}`,
    `H${round(x0)}`,
    'Z',
  ].join('')
}

/**
 * A column standing on the baseline at ``y0`` and reaching up to ``y1`` (y1 ≤ y0), left
 * edge at ``x``: square at the base, rounded at the top when it is the data end.
 */
export function verticalBar(x: number, y0: number, y1: number, thickness: number, rounded = true): string {
  const length = Math.max(0, y0 - y1)
  const r = rounded ? endRadius(thickness, length) : 0
  const top = y0 - length
  const right = x + thickness
  if (r === 0) return `M${round(x)},${round(y0)}V${round(top)}H${round(right)}V${round(y0)}Z`
  return [
    `M${round(x)},${round(y0)}`,
    `V${round(top + r)}`,
    `A${r},${r} 0 0 1 ${round(x + r)},${round(top)}`,
    `H${round(right - r)}`,
    `A${r},${r} 0 0 1 ${round(right)},${round(top + r)}`,
    `V${round(y0)}`,
    'Z',
  ].join('')
}

export type Segment = { index: number; y0: number; y1: number; top: boolean }

/**
 * The segments of a stacked column, bottom first, in screen space: each segment
 * above the first gives up the gap at its base, so the stack shows the surface
 * between fills, and only the topmost segment has the rounded data end. Segments
 * too short to show after the gap are left out.
 */
export function stackSegments(values: (number | null)[], y: (value: number) => number): Segment[] {
  const drawn: Segment[] = []
  let sum = 0
  values.forEach((value, index) => {
    if (value === null || value <= 0) return
    const base = y(sum)
    sum += value
    const y0 = drawn.length ? base - GAP : base
    const y1 = y(sum)
    if (y0 - y1 > 0) drawn.push({ index, y0, y1, top: false })
  })
  if (drawn.length) drawn[drawn.length - 1].top = true
  return drawn
}

/** A hairline at a whole pixel, offset half a pixel so it renders 1px sharp. */
export function crisp(value: number): number {
  return Math.round(value) + HAIRLINE / 2
}

/** Roughly how wide a counter-size label is, for leaving room beside an axis. */
export const labelWidth = (text: string) => Math.ceil(text.length * 6.4) + 8

/** A safe element id from React's generated ids, for SVG ``url(#…)`` references. */
export const svgId = (id: string) => `figure-${id.replace(/[^a-zA-Z0-9_-]/g, '')}`
