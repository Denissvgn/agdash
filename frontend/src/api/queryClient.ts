import { QueryClient } from '@tanstack/react-query'

/** One retry, then the page shows the error and offers its own retry. */
export function newQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: 1, staleTime: 15_000, refetchOnWindowFocus: false } },
  })
}
