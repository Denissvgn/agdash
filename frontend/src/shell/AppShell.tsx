import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { matchPath, Outlet, useLocation } from 'react-router-dom'
import { ConductorBar } from './ConductorBar'
import { Destinations } from './Destinations'
import { NavDrawer } from './NavDrawer'
import { workspaceFor } from './navigation'

/** The frame every view renders into: conductor bar, contextual sidebar, page column. */
export function AppShell() {
  const { t } = useTranslation()
  const location = useLocation()
  // A session's page is a data-heavy workbench (its overview over its timeline): it may take 1440px.
  const workbench = matchPath('/history/sessions/:sessionId', location.pathname) !== null
  const workspace = workspaceFor(location.pathname)
  const menuButton = useRef<HTMLButtonElement>(null)
  // The drawer belongs to the address it was opened at; navigating closes it.
  const [openAt, setOpenAt] = useState<string | null>(null)
  const drawerOpen = openAt === location.pathname

  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#content"
        className="sr-only z-40 rounded-control bg-working-paper px-md py-sm text-body text-primary-ink shadow-popover focus:not-sr-only focus:fixed focus:left-md focus:top-sm"
      >
        {t('shell.skipToContent')}
      </a>
      <ConductorBar
        drawerOpen={drawerOpen}
        onOpenMenu={() => setOpenAt(location.pathname)}
        menuButtonRef={menuButton}
      />
      <div className="flex flex-1">
        {workspace ? (
          <aside className="hidden w-sidebar shrink-0 border-r border-hairline bg-working-paper px-sm py-lg wide:block">
            <div className="sticky top-[calc(theme(spacing.conductor)+env(safe-area-inset-top)+theme(spacing.lg))]">
              <Destinations workspace={workspace} />
            </div>
          </aside>
        ) : null}
        <main id="content" tabIndex={-1} className="min-w-0 flex-1 focus:outline-none">
          <div className={`mx-auto ${workbench ? 'max-w-workbench' : 'max-w-page'} py-lg pl-[max(theme(spacing.phone-x),env(safe-area-inset-left))] pr-[max(theme(spacing.phone-x),env(safe-area-inset-right))] roomy:pl-[max(theme(spacing.page-x),env(safe-area-inset-left))] roomy:pr-[max(theme(spacing.page-x),env(safe-area-inset-right))]`}>
            <Outlet />
          </div>
        </main>
      </div>
      <NavDrawer
        open={drawerOpen}
        onClose={() => setOpenAt(null)}
        workspace={workspace}
        returnFocusTo={menuButton}
      />
    </div>
  )
}
