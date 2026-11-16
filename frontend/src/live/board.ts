import type { SessionState, SessionSummary } from '../api/types'

/** How the live board arranges sessions; pure, so it is tested without a browser. */

export type LiveGroup = 'folder' | 'harness'
export const LIVE_GROUPS: LiveGroup[] = ['folder', 'harness']

/** An unknown session counts as live while it was active this recently. */
export const UNKNOWN_WINDOW_MS = 30 * 60_000
/** Ended sessions stay on the board, under "Just ended", for this long. */
export const ENDED_WINDOW_MS = 15 * 60_000

export const LIVE_STATES: SessionState[] = ['active', 'waiting', 'idle', 'unknown']
const STATE_ORDER: Record<SessionState, number> = { active: 0, waiting: 1, idle: 2, unknown: 3, ended: 4 }

export interface Node {
  session: SessionSummary
  children: Node[]
  /** The parent is a session agdash knows of that is not on the board. */
  orphan: boolean
}

export interface Group {
  key: string | null
  nodes: Node[]
  /** Sessions in the group that belong to it: by harness, those of that harness. */
  count: number
  /** Sessions nested here that another harness runs (harness grouping only). */
  others: number
}

function time(value: string | null): number {
  return value ? Date.parse(value) : 0
}

export function lastActive(session: SessionSummary): number {
  return time(session.last_activity_at ?? session.started_at)
}

export function endedAt(session: SessionSummary): number {
  return time(session.ended_at) || lastActive(session)
}

function byState(a: SessionSummary, b: SessionSummary): number {
  return STATE_ORDER[a.state] - STATE_ORDER[b.state] || lastActive(b) - lastActive(a) || b.id - a.id
}

/** Live sessions and those just ended, from the rows the API returned. */
export function split(sessions: SessionSummary[], now: number): { live: SessionSummary[]; ended: SessionSummary[] } {
  const seen = new Map<number, SessionSummary>()
  for (const session of sessions) seen.set(session.id, session)
  const live: SessionSummary[] = []
  const ended: SessionSummary[] = []
  for (const session of seen.values()) {
    if (session.state === 'ended') {
      if (now - endedAt(session) <= ENDED_WINDOW_MS) ended.push(session)
    } else if (session.state !== 'unknown' || now - lastActive(session) <= UNKNOWN_WINDOW_MS) {
      live.push(session)
    }
  }
  ended.sort((a, b) => endedAt(b) - endedAt(a) || b.id - a.id)
  return { live, ended }
}

/** Sessions arranged under their parents; a child whose parent is not live stands alone. */
export function forest(sessions: SessionSummary[]): Node[] {
  const nodes = new Map<number, Node>()
  for (const session of [...sessions].sort(byState)) {
    nodes.set(session.id, { session, children: [], orphan: false })
  }
  const roots: Node[] = []
  for (const node of nodes.values()) {
    const parentId = node.session.parent_session_id
    const parent = parentId === null ? undefined : nodes.get(parentId)
    if (parent && !reaches(nodes, parent, node.session.id)) parent.children.push(node)
    else {
      node.orphan = parentId !== null
      roots.push(node)
    }
  }
  return roots
}

/** Whether walking up from ``node`` meets ``id``: a guard against a parent loop. */
function reaches(nodes: Map<number, Node>, node: Node, id: number): boolean {
  let current: Node | undefined = node
  for (let steps = 0; current && steps < 64; steps += 1) {
    if (current.session.id === id) return true
    const up: number | null = current.session.parent_session_id
    current = up === null ? undefined : nodes.get(up)
  }
  return false
}

function members(node: Node): SessionSummary[] {
  return [node.session, ...node.children.flatMap(members)]
}

function newest(node: Node): number {
  return Math.max(lastActive(node.session), ...node.children.map(newest))
}

/** Root trees grouped by the root's folder or harness; most recently active group first. */
export function groups(sessions: SessionSummary[], by: LiveGroup): Group[] {
  const found = new Map<string | null, Node[]>()
  for (const node of forest(sessions)) {
    const key = by === 'folder' ? node.session.cwd : node.session.harness
    found.set(key, [...(found.get(key) ?? []), node])
  }
  return [...found.entries()]
    .map(([key, nodes]) => {
      const all = nodes.flatMap(members)
      const own = by === 'harness' ? all.filter((session) => session.harness === key).length : all.length
      return { key, nodes, count: own, others: all.length - own }
    })
    .sort(
      (a, b) =>
        Math.max(...b.nodes.map(newest)) - Math.max(...a.nodes.map(newest)) ||
        String(a.key).localeCompare(String(b.key)),
    )
}

export function stateCounts(sessions: SessionSummary[]): [SessionState, number][] {
  return LIVE_STATES.map((state) => [state, sessions.filter((s) => s.state === state).length] as [
    SessionState,
    number,
  ]).filter(([, count]) => count > 0)
}
