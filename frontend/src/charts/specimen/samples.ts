/**
 * Synthetic figures for the specimen page: made-up names and numbers from a fixed
 * seed, so every capture is the same. No real session contributes anything here.
 */
import { timeDay, timeHour } from 'd3-time'
import type { Category, Reported, TimeData } from '../data'

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
