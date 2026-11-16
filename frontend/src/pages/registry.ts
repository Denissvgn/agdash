import type { ComponentType } from 'react'
import { ToolsPage, UsagePage } from '../analytics/pages'
import { SearchPage, SessionsPage } from '../history/pages'
import { LiveBoardPage } from '../live/LiveBoard'
import { UnmappedPage } from '../workchord/UnmappedPage'
import { WorkChordPage } from '../workchord/WorkChordPage'

/**
 * Destinations that show real data, keyed "<workspace>:<destination>". Every other
 * destination shows the unavailable-state panel until its data source exists.
 */
export const PAGES: Record<string, ComponentType> = {
  'live:board': LiveBoardPage,
  'history:sessions': SessionsPage,
  'history:search': SearchPage,
  'analytics:usage': UsagePage,
  'analytics:tools': ToolsPage,
  'projects:unmapped': UnmappedPage,
  'settings:workchord': WorkChordPage,
}
