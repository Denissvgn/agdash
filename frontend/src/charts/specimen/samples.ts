/**
 * Synthetic figures for the specimen page: made-up names and numbers from a fixed
 * seed, so every capture is the same. No real session contributes anything here.
 */
import { timeDay, timeHour } from 'd3-time'
import type { Category, Reported, TimeData } from '../data'
import type { HeatmapData } from '../Heatmap'
import type { Lane, LaneItem } from '../Lanes'
import type { Strip } from '../StateStrips'
import type { StripState } from '../states'
import type { EvidenceTier } from '../tiers'
import type { TreeNode } from '../Tree'

/** A small seeded generator (mulberry32), so the specimen never changes between runs. */
function random(seed: number) {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const START = new Date(2026, 9, 5)
const HARNESSES = ['Claude Code', 'Codex', 'Antigravity', 'Pi']
const TOOLS = ['Read', 'Edit', 'Bash', 'Grep', 'Write', 'Glob', 'Fetch', 'Task']

function days(count: number) {
  return timeDay.range(START, timeDay.offset(START, count))
}

function series(names: string[], buckets: number, seed: number, scale: number, gaps: [number, number][] = []) {
  const next = random(seed)
  return names.map((name, s) => ({
    id: `s${s}`,
    name,
    values: Array.from({ length: buckets }, (_, i): Reported => {
      if (gaps.some(([from, to]) => i >= from && i <= to)) return null
      const weekly = i % 7 === 5 || i % 7 === 6 ? 0.35 : 1
      return Math.round(scale * weekly * (0.4 + next()) / (s + 1))
    }),
  }))
}

export function categories(dense = false, unreported = false): Category[] {
  const next = random(dense ? 7 : 3)
  const names = dense ? Array.from({ length: 34 }, (_, i) => `tool-${String(i + 1).padStart(2, '0')}`) : TOOLS
  return names.map((name, i) => ({
    id: name,
    name,
    value: unreported && (i === 2 || i === 5) ? null : Math.round(2400 * next() + 30),
  }))
}

export function daily(names = HARNESSES.slice(0, 1), options: { buckets?: number; gaps?: [number, number][]; seed?: number } = {}): TimeData {
  const buckets = days(options.buckets ?? 14)
  return { buckets, step: 86_400_000, series: series(names, buckets.length, options.seed ?? 11, 180_000, options.gaps) }
}

export function hourly(): TimeData {
  const buckets = timeHour.range(START, timeHour.offset(START, 48))
  return { buckets, step: 3_600_000, series: series(HARNESSES.slice(0, 2), buckets.length, 5, 40) }
}

export const harnesses = HARNESSES
export const manyNames = [...HARNESSES, 'Harness E', 'Harness F', 'Harness G', 'Harness H', 'Harness I']

const MINUTE = 60_000
const at = (day: number, hour: number, minute = 0) => new Date(2026, 9, 5 + day, hour, minute)

/** Synthetic agents' work: spans and moments, with a lunch break and nights between days. */
export function lanes(dense = false): { lanes: Lane[]; now: Date } {
  const next = random(dense ? 21 : 13)
  const names = dense ? ['Main agent', 'Explorer', 'Test runner', 'Reviewer', 'Docs writer', 'Planner'] : ['Main agent', 'Explorer', 'Test runner']
  const days = dense ? 3 : 1
  const result = names.map((name, l) => {
    const items: LaneItem[] = []
    for (let day = 0; day < days; day += 1) {
      for (const [from, to] of [
        [9, 12.5],
        [13.75, 17.5],
      ]) {
        let cursor = from * 60 + Math.round(next() * 20) + l * 7
        while (cursor < to * 60 - 20) {
          const length = 8 + Math.round(next() * (dense ? 30 : 50))
          const start = at(day, 0, cursor)
          items.push({ id: `${day}-${cursor}`, label: next() > 0.5 ? 'Editing files' : 'Running tests', start, end: new Date(start.getTime() + length * MINUTE) })
          if (next() > 0.6) items.push({ id: `${day}-${cursor}-p`, label: 'Asked for input', start: new Date(start.getTime() + (length + 6) * MINUTE) })
          cursor += 8
          cursor += length + 10 + Math.round(next() * 40)
        }
      }
    }
    return { id: `lane-${l}`, name, items }
  })
  return { lanes: result, now: at(days - 1, 17, 40) }
}

/** Synthetic sessions' states over an afternoon, every state shown. */
export function strips(dense = false): Strip[] {
  const next = random(dense ? 31 : 17)
  const order: StripState[] = ['active', 'waiting', 'active', 'idle', 'unknown', 'active', 'waiting', 'idle']
  const count = dense ? 12 : 4
  return Array.from({ length: count }, (_, s) => {
    let cursor = at(0, 13).getTime() + s * 9 * MINUTE
    const end = at(0, 17).getTime() - (s % 3) * 25 * MINUTE
    const segments: Strip['segments'] = []
    let i = s % order.length
    while (cursor < end) {
      const length = (6 + Math.round(next() * 34)) * MINUTE
      const state = order[i % order.length]
      segments.push({ state, start: new Date(cursor), end: new Date(Math.min(end, cursor + length)) })
      cursor += length
      i += 1
    }
    if (s % 2 === 0) segments.push({ state: 'ended', start: new Date(end), end: new Date(end + 10 * MINUTE) })
    return { id: `session-${s}`, name: `Session ${String.fromCharCode(65 + s)}`, segments }
  })
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/** Synthetic tool calls by weekday and hour. */
export function heatmap(options: { dense?: boolean; unreported?: boolean } = {}): HeatmapData {
  const next = random(options.dense ? 41 : 19)
  const rows = options.dense
    ? Array.from({ length: 16 }, (_, i) => ({ id: `t${i}`, name: `tool-${String(i + 1).padStart(2, '0')}` }))
    : WEEKDAYS.map((name) => ({ id: name, name }))
  const columns = Array.from({ length: 24 }, (_, h) => ({ id: `h${h}`, name: String(h).padStart(2, '0') }))
  const values = rows.map((_, r) =>
    columns.map((_, h): Reported => {
      if (options.unreported && r === 2 && h >= 9 && h <= 12) return null
      const working = h >= 9 && h <= 18 ? 1 : h >= 7 && h <= 21 ? 0.3 : 0.02
      const weekend = !options.dense && r >= 5 ? 0.25 : 1
      return Math.round(120 * working * weekend * next())
    }),
  )
  return { rows, columns, values }
}

/** A synthetic session and the agents it started, linked by every evidence tier. */
export function agentTree(dense = false): TreeNode {
  if (!dense) {
    return {
      id: 'root',
      name: 'Session A (Claude Code)',
      children: [
        { id: 'a', name: 'Explorer', edge: { tier: 'tool_call', label: 'Task call' }, children: [{ id: 'a1', name: 'Grep worker', edge: { tier: 'trace_id', label: 'Same trace' } }] },
        { id: 'b', name: 'Test runner', edge: { tier: 'trace_id', label: 'Same trace' } },
        { id: 'c', name: 'Codex session', edge: { tier: 'mcp_config', label: 'MCP server' }, children: [{ id: 'c1', name: 'Pi session', edge: { tier: 'heuristic', label: 'Same folder' } }] },
      ],
    }
  }
  const tiers: EvidenceTier[] = ['tool_call', 'trace_id', 'mcp_config', 'heuristic']
  const grow = (id: string, depth: number): TreeNode[] =>
    depth > 2
      ? []
      : Array.from({ length: depth === 0 ? 4 : 2 }, (_, i) => ({
          id: `${id}-${i}`,
          name: `Agent ${id.replace(/\D/g, '')}${i + 1}`,
          edge: { tier: tiers[(i + depth) % 4] },
          children: grow(`${id}-${i}`, depth + 1),
        }))
  return { id: 'r', name: 'Session B (Codex)', children: grow('r', 0) }
}
