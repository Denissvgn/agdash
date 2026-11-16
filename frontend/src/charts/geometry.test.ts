import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { foldSeries, rankCategories, unreportedRuns } from './data'
import { formatCompact, formatTick } from './format'
import {
  AREA_OPACITY,
  BAR_MAX,
  barThickness,
  DATA_END,
  DOT,
  GAP,
  HAIRLINE,
  HATCH_OPACITY,
  HATCH_SPACING,
  horizontalBar,
  LINE,
  RING,
  stackSegments,
  verticalBar,
} from './geometry'

// The marks' rules as DESIGN.md states them (Figures: Furniture and marks; Roles).
const design = {
  barMax: 24,
  dataEnd: 4,
  gap: 2,
  line: 2,
  dot: 8,
  ring: 2,
  hairline: 1,
  area: 0.1,
  hatchSpacing: 6,
  hatchOpacity: 0.35,
}

/** The commands of an SVG path, each with its numbers. */
function commands(path: string) {
  return [...path.matchAll(/([MHVAZ])([^MHVAZ]*)/g)].map(([, op, args]) => ({
    op,
    n: (args.match(/-?\d+(\.\d+)?/g) ?? []).map(Number),
  }))
}

describe('mark geometry follows DESIGN.md', () => {
  test('DESIGN.md states the values this test holds the marks to', () => {
    const figures = readFileSync(resolve(__dirname, '../../../DESIGN.md'), 'utf8').split('## Figures')[1]
    for (const phrase of [
      'at most 24px thick',
      'a 4px rounded data end and a square base',
      'a 2px gap of the surface colour',
      'Lines are 2px with round joins',
      'dots are at least 8px with a 2px surface ring',
      "an area is its line's colour at 10%",
      'solid and 1px',
      '1px strong-rule lines at 35% opacity, 6px apart',
    ]) {
      expect(figures).toContain(phrase)
    }
  })

  test('the constants are the stated values', () => {
    expect({
      barMax: BAR_MAX,
      dataEnd: DATA_END,
      gap: GAP,
      line: LINE,
      dot: DOT,
      ring: RING,
      hairline: HAIRLINE,
      area: AREA_OPACITY,
      hatchSpacing: HATCH_SPACING,
      hatchOpacity: HATCH_OPACITY,
    }).toEqual(design)
  })

  test('a bar is never thicker than 24px and always leaves the gap to its neighbour', () => {
    expect(barThickness(80)).toBe(24)
    expect(barThickness(20)).toBe(18)
    expect(barThickness(2.5)).toBe(1)
  })

  test('a horizontal bar has a square base on the baseline and a 4px rounded data end', () => {
    const path = commands(horizontalBar(40, 200, 10, 20))
    expect(path[0]).toEqual({ op: 'M', n: [40, 10] })
    const arcs = path.filter((c) => c.op === 'A')
    expect(arcs).toHaveLength(2)
    for (const arc of arcs) expect(arc.n.slice(0, 2)).toEqual([4, 4])
    // Both arcs sit at the data end, 200; the base at 40 is a straight edge.
    expect(arcs.map((arc) => arc.n[5])).toEqual([200, 196])
    expect(path[path.length - 2]).toEqual({ op: 'H', n: [40] })
  })

  test('a column has a square base and rounds only its top', () => {
    const path = commands(verticalBar(10, 150, 50, 24))
    expect(path[0]).toEqual({ op: 'M', n: [10, 150] })
    const arcs = path.filter((c) => c.op === 'A')
    expect(arcs.map((arc) => arc.n.slice(0, 2))).toEqual([
      [4, 4],
      [4, 4],
    ])
    expect(arcs.map((arc) => arc.n[6])).toEqual([50, 54])
    expect(path.filter((c) => c.op === 'V').at(-1)).toEqual({ op: 'V', n: [150] })
  })

  test('a mark too short for the full corner gets a smaller one, never a bulge', () => {
    const arcs = commands(verticalBar(0, 100, 97, 24)).filter((c) => c.op === 'A')
    for (const arc of arcs) expect(arc.n[0]).toBe(3)
    expect(commands(verticalBar(0, 100, 100, 24)).some((c) => c.op === 'A')).toBe(false)
  })

  test('stacked segments are parted by a 2px gap, and only the top one is rounded', () => {
    const y = (value: number) => 200 - value
    const segments = stackSegments([50, null, 30, 20], y)
    expect(segments.map((s) => s.index)).toEqual([0, 2, 3])
    expect(segments[0].y0).toBe(200)
    for (let i = 1; i < segments.length; i += 1) expect(segments[i - 1].y1 - segments[i].y0).toBe(2)
    expect(segments.map((s) => s.top)).toEqual([false, false, true])
  })
})

describe('figure data', () => {
  test('a value that was not reported stays apart from zero', () => {
    expect(unreportedRuns([1, null, null, 0, null])).toEqual([
      [1, 2],
      [4, 4],
    ])
  })

  test('ranked bars fold their tail into Other and keep not-reported last', () => {
    const ranked = rankCategories(
      [
        { id: 'a', name: 'a', value: 5 },
        { id: 'b', name: 'b', value: null },
        { id: 'c', name: 'c', value: 9 },
        { id: 'd', name: 'd', value: 1 },
      ],
      3,
      'Other',
    )
    expect(ranked.map((row) => [row.id, row.value])).toEqual([
      ['c', 9],
      ['a', 5],
      ['__other__', 1],
    ])
  })

  test('series past the limit fold into Other bucket by bucket, never inventing a zero', () => {
    const folded = foldSeries(
      [
        { id: 'a', name: 'a', values: [1, 2] },
        { id: 'b', name: 'b', values: [3, null] },
        { id: 'c', name: 'c', values: [4, null] },
      ],
      2,
      'Other',
    )
    expect(folded.map((series) => series.values)).toEqual([
      [1, 2],
      [7, null],
    ])
  })

  test('axis numbers are compact English, and time labels use the coarsest unit that changes', () => {
    expect([formatCompact(1200), formatCompact(34_000_000), formatCompact(0.5)]).toEqual(['1.2K', '34M', '0.5'])
    expect(formatTick(new Date(2026, 9, 5), 86_400_000)).toBe('Oct 5')
    expect(formatTick(new Date(2026, 9, 5, 14), 3_600_000)).toBe('14:00')
    expect(formatTick(new Date(2026, 9, 1), 30 * 86_400_000)).toBe('Oct 2026')
  })
})
