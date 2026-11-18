import { QueryClientContext, type QueryClient } from '@tanstack/react-query'
import { useContext, useSyncExternalStore } from 'react'

/** How often the app asks whether the service has finished preparing its store. */
export const PREPARING_ASK_MS = 5000

type Watch = { preparing: boolean; timer: ReturnType<typeof setInterval> | null; listeners: Set<() => void> }

// One watch per query client, so an app instance (and each test) has its own.
const watches = new WeakMap<QueryClient, Watch>()

function watchOf(client: QueryClient): Watch {
  let watch = watches.get(client)
  if (!watch) {
    watch = { preparing: false, timer: null, listeners: new Set() }
    watches.set(client, watch)
  }
  return watch
}

function set(watch: Watch, preparing: boolean) {
  if (watch.preparing === preparing) return
  watch.preparing = preparing
  watch.listeners.forEach((listener) => listener())
}

/** Whether the service still says it is preparing. Anything else, silence included, is not that. */
async function stillPreparing(): Promise<boolean> {
  try {
    const response = await fetch(`${window.__AGDASH__?.apiBase ?? ''}/health`, { headers: { Accept: 'application/json' } })
    if (!response.ok) return response.status === 503
    const body = (await response.json()) as { status?: unknown } | null
    return body?.status === 'preparing'
  } catch {
    return false
  }
}

/**
 * The service answered that it is preparing its store. Until it says otherwise, every
 * page and figure that failed for that reason says so instead of offering a retry that
 * cannot help; once it is ready, everything is asked for again, with nothing clicked.
 */
export function watchUntilReady(client: QueryClient, askEvery = PREPARING_ASK_MS) {
  const watch = watchOf(client)
  set(watch, true)
  if (watch.timer !== null) return
  const stop = () => {
    if (watch.timer !== null) clearInterval(watch.timer)
    watch.timer = null
    set(watch, false)
  }
  watch.timer = setInterval(() => {
    // Nothing on screen asks any more: the app was closed, so the watch ends with it.
    if (!client.getQueryCache().getAll().some((query) => query.getObserversCount() > 0)) return stop()
    void stillPreparing().then((preparing) => {
      if (preparing || watch.timer === null) return
      stop()
      void client.invalidateQueries()
    })
  }, askEvery)
}

// For a figure drawn outside the app, with no query client: nothing is ever preparing.
const NEVER: Watch = { preparing: false, timer: null, listeners: new Set() }

/** Whether the service is known to be preparing its store right now. */
export function usePreparing(): boolean {
  const client = useContext(QueryClientContext)
  const watch = client ? watchOf(client) : NEVER
  return useSyncExternalStore(
    (listener) => {
      watch.listeners.add(listener)
      return () => watch.listeners.delete(listener)
    },
    () => watch.preparing,
  )
}
