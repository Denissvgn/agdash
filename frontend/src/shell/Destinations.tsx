import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import { destinationKey, type Workspace } from './navigation'

interface Props {
  workspace: Workspace
  onNavigate?: () => void
}

/** The current workspace's own destinations, used by the sidebar and the drawer. */
export function Destinations({ workspace, onNavigate }: Props) {
  const { t } = useTranslation()
  const { pathname } = useLocation()
  const here = pathname.replace(/\/+$/, '') || '/'
  const label = t(`workspaces.${workspace.id}.label`)
  return (
    <nav aria-label={t('shell.sections', { workspace: label })}>
      <h2 className="px-sm pb-sm text-metadata font-medium text-secondary-ink">{label}</h2>
      <ul className="flex flex-col gap-[2px]">
        {workspace.destinations.map((destination) => {
          const page = here === destination.path
          // A page under the destination, such as one session's: marked, so the sidebar
          // still says where the reader is, but as the place and not the page itself.
          const within = !page && destination.also !== undefined && pathname.startsWith(destination.also)
          return (
            <li key={destination.id}>
              <Link
                aria-current={page ? 'page' : within ? 'true' : undefined}
                className={
                  'flex h-control items-center rounded-control px-sm text-body transition-colors duration-state pointer-coarse:h-touch ' +
                  (page || within
                    ? 'bg-planning-thread-soft font-medium text-planning-thread shadow-[inset_1px_0_0_theme(colors.planning-thread)]'
                    : 'text-secondary-ink hover:bg-canvas-mist hover:text-primary-ink')
                }
                to={destination.path}
                onClick={onNavigate}
              >
                {t(`${destinationKey(workspace, destination)}.label`)}
              </Link>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
