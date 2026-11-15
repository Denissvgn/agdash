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
}

export const NO_FILTERS: Filters = { harness: [], model: '', folder: '', auth: '', from: '', to: '' }

const DAY = /^\d{4}-\d{2}-\d{2}$/

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
  }
}

/** ``search`` with ``filters`` written into it; other parameters (such as the query) stay. */
export function writeFilters(search: URLSearchParams, filters: Filters): URLSearchParams {
  const next = new URLSearchParams(search)
  for (const key of ['harness', 'model', 'folder', 'auth', 'from', 'to']) next.delete(key)
  filters.harness.forEach((harness) => next.append('harness', harness))
  for (const key of ['model', 'folder', 'auth', 'from', 'to'] as const) {
    if (filters[key]) next.set(key, filters[key])
  }
  return next
}

export function isFiltered(filters: Filters): boolean {
  return Boolean(
    filters.harness.length || filters.model || filters.folder || filters.auth || filters.from || filters.to,
  )
}

/** The start of a local calendar day, as an instant the API understands. */
function localDayStart(day: string, addDays = 0): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date + addDays).toISOString()
}

export function apiParams(filters: Filters): Params {
  return {
    harness: filters.harness,
    model: filters.model,
    folder: filters.folder,
    auth_method: filters.auth,
    started_from: filters.from ? localDayStart(filters.from) : undefined,
    started_before: filters.to ? localDayStart(filters.to, 1) : undefined,
  }
}
