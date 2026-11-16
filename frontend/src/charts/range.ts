/** A selection over time buckets: the first and last bucket in it, by index. */
export type Range = [number, number]

/** Keeps a range inside ``0..last`` with its start at or before its end. */
export function clampRange([start, end]: Range, last: number): Range {
  const a = Math.max(0, Math.min(last, Math.round(start)))
  const b = Math.max(0, Math.min(last, Math.round(end)))
  return a <= b ? [a, b] : [b, a]
}

/** Moves a whole range by ``by`` buckets, keeping its width and staying inside ``0..last``. */
export function shiftRange([start, end]: Range, by: number, last: number): Range {
  const width = end - start
  const from = Math.max(0, Math.min(last - width, start + by))
  return [from, from + width]
}
