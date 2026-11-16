import { useQuery } from '@tanstack/react-query'
import { ApiError, getJson } from '../api/client'
import type { ProjectOption, WorkChordConnection } from '../api/types'

export const CONNECTIONS = ['workchord', 'connections'] as const

export function useConnections() {
  return useQuery({
    queryKey: CONNECTIONS,
    queryFn: ({ signal }) => getJson<WorkChordConnection[]>('/api/workchord/connections', {}, signal),
  })
}

export function useProjects(connectionId: number | null) {
  return useQuery({
    queryKey: ['workchord', 'projects', connectionId],
    queryFn: ({ signal }) =>
      getJson<ProjectOption[]>(`/api/workchord/connections/${connectionId}/projects`, {}, signal),
    enabled: connectionId !== null,
  })
}

/** What went wrong, in the service's words when it gave some. */
export function reasonOf(error: unknown): string | null {
  return error instanceof ApiError ? error.detail : null
}
