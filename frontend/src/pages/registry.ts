import type { ComponentType } from 'react'
import { ActivityPage } from '../analytics/ActivityPage'
import { ToolsPage, UsagePage } from '../analytics/pages'
import { SearchPage, SessionsPage } from '../history/pages'
import { LiveAgentsPage, LiveBoardPage } from '../live/LiveBoard'
import { ProjectBoardPage } from '../workchord/ProjectBoardPage'
import { UnmappedPage } from '../workchord/UnmappedPage'
import { WorkChordPage } from '../workchord/WorkChordPage'

/**
 * Destinations that show real data, keyed "<workspace>:<destination>". Every other
 * destination shows the unavailable-state panel until its data source exists.
 */
export const PAGES: Record<string, ComponentType> = {
  'live:board': LiveBoardPage,
  'live:agents': LiveAgentsPage,
  'history:sessions': SessionsPage,
  'history:search': SearchPage,
  'analytics:usage': UsagePage,
  'analytics:tools': ToolsPage,
  'analytics:activity': ActivityPage,
  'projects:board': ProjectBoardPage,
  'projects:unmapped': UnmappedPage,
  'settings:workchord': WorkChordPage,
}
