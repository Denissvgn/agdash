/**
 * Tailwind classes for the figure roles, written out in full so the build keeps them.
 * Colours come only from the figure layer and the system's own tokens.
 */

export type Ink = 'ink-1' | 'ink-2' | 'ink-3' | 'ink-4' | 'ink-5' | 'ink-6' | 'ink-7'

export const FILL: Record<Ink | 'mark' | 'mark-hover' | 'context' | 'other' | 'selected', string> = {
  'ink-1': 'fill-figure-ink-1',
  'ink-2': 'fill-figure-ink-2',
  'ink-3': 'fill-figure-ink-3',
  'ink-4': 'fill-figure-ink-4',
  'ink-5': 'fill-figure-ink-5',
  'ink-6': 'fill-figure-ink-6',
  'ink-7': 'fill-figure-ink-7',
  mark: 'fill-figure-mark',
  'mark-hover': 'fill-figure-mark-hover',
  context: 'fill-figure-context',
  other: 'fill-figure-other',
  selected: 'fill-planning-thread',
}

export const STROKE: Record<Ink | 'mark' | 'mark-hover' | 'selected', string> = {
  'ink-1': 'stroke-figure-ink-1',
  'ink-2': 'stroke-figure-ink-2',
  'ink-3': 'stroke-figure-ink-3',
  'ink-4': 'stroke-figure-ink-4',
  'ink-5': 'stroke-figure-ink-5',
  'ink-6': 'stroke-figure-ink-6',
  'ink-7': 'stroke-figure-ink-7',
  mark: 'stroke-figure-mark',
  'mark-hover': 'stroke-figure-mark-hover',
  selected: 'stroke-planning-thread',
}
