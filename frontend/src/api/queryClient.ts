import { QueryCache, QueryClient } from '@tanstack/react-query'
import { ApiError } from './client'
import { watchUntilReady } from './preparing'

const preparing = (error: unknown) => error instanceof ApiError && error.kind === 'preparing'

/**
 * One retry, then the page shows the error and offers its own retry. A service that
 * says it is preparing its store is not retried: the app watches for it to be ready
 * and then asks for everything again, looking every ``preparingAskMs``.
 */
export function newQueryClient(preparingAskMs?: number) {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => {
        if (preparing(error)) watchUntilReady(client, preparingAskMs)
      },
    }),
    defaultOptions: {
      queries: {
        retry: (failures, error) => !preparing(error) && failures < 1,
        staleTime: 15_000,
        refetchOnWindowFocus: false,
      },
    },
  })
  return client
}
