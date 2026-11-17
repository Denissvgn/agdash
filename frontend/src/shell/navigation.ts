import type { LucideIcon } from 'lucide-react'
import { Activity, ChartColumn, FolderKanban, History, Settings } from 'lucide-react'

export type WorkspaceId = 'live' | 'history' | 'projects' | 'analytics' | 'settings'

export interface Destination {
  id: string
  path: string
  /** Pages under this address belong to the destination too, such as one session's page. */
  also?: string
}

export interface Workspace {
  id: WorkspaceId
  path: string
  icon: LucideIcon
  destinations: Destination[]
}

/**
 * The workspace taxonomy: the conductor bar switches workspaces, the sidebar lists
 * the current workspace's destinations. A workspace's first destination lives at
 * the workspace's own path. Labels and copy live in the message catalogue under
 * workspaces.<id>.
 */
export const WORKSPACES: Workspace[] = [
  {
    id: 'live',
    path: '/live',
    icon: Activity,
    destinations: [
      { id: 'board', path: '/live' },
      { id: 'agents', path: '/live/agents' },
    ],
  },
  {
    id: 'history',
    path: '/history',
    icon: History,
    destinations: [
      { id: 'sessions', path: '/history', also: '/history/sessions/' },
      { id: 'search', path: '/history/search' },
    ],
  },
  {
    id: 'projects',
    path: '/projects',
    icon: FolderKanban,
    destinations: [
      { id: 'board', path: '/projects' },
      { id: 'unmapped', path: '/projects/unmapped' },
    ],
  },
  {
    id: 'analytics',
    path: '/analytics',
    icon: ChartColumn,
    destinations: [
      { id: 'usage', path: '/analytics' },
      { id: 'tools', path: '/analytics/tools' },
      { id: 'activity', path: '/analytics/activity' },
    ],
  },
  {
    id: 'settings',
    path: '/settings',
    icon: Settings,
    destinations: [
      { id: 'sources', path: '/settings' },
      { id: 'workchord', path: '/settings/workchord' },
      { id: 'data', path: '/settings/data' },
      { id: 'service', path: '/settings/service' },
    ],
  },
]

export const HOME_PATH = WORKSPACES[0].path

export function workspaceFor(pathname: string): Workspace | undefined {
  return WORKSPACES.find(
    (workspace) => pathname === workspace.path || pathname.startsWith(`${workspace.path}/`),
  )
}

export function destinationKey(workspace: Workspace, destination: Destination): string {
  return `workspaces.${workspace.id}.destinations.${destination.id}`
}
