import type { Params } from '../api/client'

/**
 * Session filters live in the address, so a filtered list can be reloaded,
 * bookmarked and shared, and Back undoes a change.
 */
export interface Filters {
  harness: string[]
  model: string
  folder: string
  auth: string
  from: string // YYYY-MM-DD, a local day
  to: string // YYYY-MM-DD, a local day, included
  /** A preset that follows today: the last 7, 30 or 90 days. It replaces from and to. */
  range: Preset | ''
}

export const PRESETS = ['7', '30', '90'] as const
export type Preset = (typeof PRESETS)[number]

export const NO_FILTERS: Filters = { harness: [], model: '', folder: '', auth: '', from: '', to: '', range: '' }

const DAY = /^\d{4}-\d{2}-\d{2}$/

/** A local day as YYYY-MM-DD. */
export function dayOf(at: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`
}

/** The days a filter covers: a preset counts back from today, today included. */
export function datesOf(filters: Filters, today = new Date()): { from: string; to: string } {
  if (!filters.range) return { from: filters.from, to: filters.to }
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (Number(filters.range) - 1))
  return { from: dayOf(start), to: dayOf(today) }
}

export function readFilters(search: URLSearchParams): Filters {
  const day = (key: string) => {
    const value = search.get(key) ?? ''
    return DAY.test(value) ? value : ''
  }
  return {
    harness: search.getAll('harness').filter(Boolean),
    model: search.get('model') ?? '',
    folder: search.get('folder') ?? '',
    auth: search.get('auth') ?? '',
    from: day('from'),
    to: day('to'),
    range: (PRESETS as readonly string[]).includes(search.get('range') ?? '') ? (search.get('range') as Preset) : '',
  }
}

/** ``search`` with ``filters`` written into it; other parameters (such as the query) stay. */
export function writeFilters(search: URLSearchParams, filters: Filters): URLSearchParams {
  const next = new URLSearchParams(search)
  for (const key of ['harness', 'model', 'folder', 'auth', 'from', 'to', 'range']) next.delete(key)
  filters.harness.forEach((harness) => next.append('harness', harness))
  for (const key of ['model', 'folder', 'auth'] as const) {
    if (filters[key]) next.set(key, filters[key])
  }
  // A preset stands for its days, so the address keeps the preset rather than dates that go stale.
  if (filters.range) next.set('range', filters.range)
  else {
    if (filters.from) next.set('from', filters.from)
    if (filters.to) next.set('to', filters.to)
  }
  return next
}

export function isFiltered(filters: Filters): boolean {
  return Boolean(
    filters.harness.length || filters.model || filters.folder || filters.auth || filters.from || filters.to || filters.range,
  )
}

/** The start of a local calendar day, as an instant the API understands. */
function localDayStart(day: string, addDays = 0): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date + addDays).toISOString()
}

export function apiParams(filters: Filters, today = new Date()): Params {
  const { from, to } = datesOf(filters, today)
  return {
    harness: filters.harness,
    model: filters.model,
    folder: filters.folder,
    auth_method: filters.auth,
    started_from: from ? localDayStart(from) : undefined,
    started_before: to ? localDayStart(to, 1) : undefined,
  }
}
