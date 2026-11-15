import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { getJson } from '../api/client'
import type { Facets } from '../api/types'
import { readFilters, writeFilters, type Filters } from './filters'

/** The values the session filters can take, shared by every page that filters sessions. */
export function useFacets() {
  return useQuery({
    queryKey: ['session-facets'],
    queryFn: ({ signal }) => getJson<Facets>('/api/sessions/facets', {}, signal),
    staleTime: 60_000,
  })
}

/** The filters in the address, and a way to change them that keeps the rest of it. */
export function useAddressFilters(): [Filters, (filters: Filters) => void, URLSearchParams] {
  const [search, setSearch] = useSearchParams()
  const filters = readFilters(search)
  const change = (next: Filters) => setSearch(writeFilters(search, next))
  return [filters, change, search]
}
