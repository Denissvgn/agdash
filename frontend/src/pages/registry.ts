import type { ComponentType } from 'react'
import { ToolsPage, UsagePage } from '../analytics/pages'
import { SearchPage, SessionsPage } from '../history/pages'

/**
 * Destinations that show real data, keyed "<workspace>:<destination>". Every other
 * destination shows the unavailable-state panel until its data source exists.
 */
export const PAGES: Record<string, ComponentType> = {
  'history:sessions': SessionsPage,
  'history:search': SearchPage,
  'analytics:usage': UsagePage,
  'analytics:tools': ToolsPage,
}
