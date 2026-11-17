/**
 * A time axis that keeps busy stretches to scale and squeezes long idle gaps into a
 * fixed break, so a day of work with a night between still reads on one axis.
 */

/** A compressed gap takes this many pixels, whatever its length. */
export const BREAK = 18
/** A busy stretch is never narrower than this share of the room, so a lone moment still shows. */
const MIN_SHARE = 0.02

export type Stretch = { from: number; to: number; left: number; right: number }
export type Gap = { from: number; to: number; x: number }

export type CompressedScale = {
  (at: number): number
  /** The time at a position; a position inside a break is the end of the gap it stands for. */
  invert: (x: number) => number
  stretches: Stretch[]
  gaps: Gap[]
}

/**
 * Times in milliseconds, merged into stretches wherever they lie within ``threshold``
 * of each other; each gap longer than that becomes a break of ``BREAK`` pixels, as many
 * as fit in a third of the range.
 */
export function compressedScale(intervals: [number, number][], threshold: number, range: [number, number]): CompressedScale {
  const sorted = intervals
    .map(([from, to]): [number, number] => [Math.min(from, to), Math.max(from, to)])
    .sort((a, b) => a[0] - b[0])
  const merged: [number, number][] = []
  for (const [from, to] of sorted) {
    const last = merged[merged.length - 1]
    if (last && from - last[1] <= threshold) last[1] = Math.max(last[1], to)
    else merged.push([from, to])
  }
  if (!merged.length) merged.push([0, 1])

  const [start, end] = range
  // Breaks never take more than a third of the room: past that, the shortest gaps are
  // kept to scale instead, so many short idle spells cannot push the busy time off the axis.
  const most = Math.floor((end - start) / 3 / BREAK)
  while (merged.length - 1 > Math.max(0, most)) {
    let shortest = 0
    for (let i = 1; i < merged.length - 1; i++) {
      if (merged[i + 1][0] - merged[i][1] < merged[shortest + 1][0] - merged[shortest][1]) shortest = i
    }
    merged[shortest][1] = Math.max(merged[shortest][1], merged[shortest + 1][1])
    merged.splice(shortest + 1, 1)
  }
  const room = Math.max(1, end - start - BREAK * (merged.length - 1))
  const total = merged.reduce((sum, [from, to]) => sum + (to - from), 0)
  const weights = merged.map(([from, to]) => (total > 0 ? Math.max((to - from) / total, MIN_SHARE) : 1 / merged.length))
  const sum = weights.reduce((a, b) => a + b, 0)

  const stretches: Stretch[] = []
  const gaps: Gap[] = []
  let left = start
  merged.forEach(([from, to], i) => {
    const width = (room * weights[i]) / sum
    stretches.push({ from, to, left, right: left + width })
    left += width
    if (i < merged.length - 1) {
      gaps.push({ from: to, to: merged[i + 1][0], x: left + BREAK / 2 })
      left += BREAK
    }
  })

  const scale = ((at: number) => {
    const inside = stretches.find((stretch) => at <= stretch.to) ?? stretches[stretches.length - 1]
    if (at < inside.from) {
      // In a gap: hold at the break before this stretch.
      const gap = gaps.find((g) => g.to === inside.from)
      return gap ? gap.x : inside.left
    }
    if (inside.to === inside.from) return (inside.left + inside.right) / 2
    const share = Math.min(1, (at - inside.from) / (inside.to - inside.from))
    return inside.left + share * (inside.right - inside.left)
  }) as CompressedScale
  scale.invert = (px: number) => {
    const inside = stretches.find((stretch) => px <= stretch.right) ?? stretches[stretches.length - 1]
    if (px < inside.left) return inside.from
    if (inside.right === inside.left) return inside.from
    const share = Math.min(1, Math.max(0, (px - inside.left) / (inside.right - inside.left)))
    return inside.from + share * (inside.to - inside.from)
  }
  scale.stretches = stretches
  scale.gaps = gaps
  return scale
}
