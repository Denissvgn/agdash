import { type QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { i18n as I18n } from 'i18next'
import { useState } from 'react'
import { I18nextProvider } from 'react-i18next'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { newQueryClient } from './api/queryClient'
import { i18n as defaultI18n } from './i18n'
import { DestinationPage } from './pages/DestinationPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { PAGES } from './pages/registry'
import { AppShell } from './shell/AppShell'
import { HOME_PATH, WORKSPACES } from './shell/navigation'

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to={HOME_PATH} replace />} />
        {WORKSPACES.flatMap((workspace) =>
          workspace.destinations.map((destination) => {
            const Page = PAGES[`${workspace.id}:${destination.id}`]
            return (
              <Route
                key={`${workspace.id}:${destination.id}`}
                path={destination.path}
                element={Page ? <Page /> : <DestinationPage workspace={workspace} destination={destination} />}
              />
            )
          }),
        )}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

// A hash router works the same in the desktop window, in a browser, and behind an
// SSH tunnel, with no server-side route configuration.
export default function App({ i18n = defaultI18n, queryClient }: { i18n?: I18n; queryClient?: QueryClient }) {
  const [client] = useState(() => queryClient ?? newQueryClient())
  return (
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <HashRouter>
          <AppRoutes />
        </HashRouter>
      </I18nextProvider>
    </QueryClientProvider>
  )
}
