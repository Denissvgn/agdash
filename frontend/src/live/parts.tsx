import type { HeatScale } from '../charts/heat'
import type { LiveRow } from './Strips'

/** The last 30 minutes of the board's sessions, for the rows' lanes. */
export type Strips = { rows: Map<number, LiveRow>; scale: HeatScale; from: number; until: number }

/** The address that shows one session's details beside the board, keeping the rest of it. */
export type LinkTo = (sessionId: number | null) => string

export function Folder({ path }: { path: string }) {
  // Cut from the start: the last folders are what tell sessions apart.
  return (
    <span className="data block min-w-0 truncate text-left font-mono text-[0.75rem] [direction:rtl]">
      <bdi>{path}</bdi>
    </span>
  )
}
