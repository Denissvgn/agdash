import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router-dom'
import { destinationKey, type Workspace } from './navigation'

interface Props {
  workspace: Workspace
  onNavigate?: () => void
}

/** The current workspace's own destinations, used by the sidebar and the drawer. */
export function Destinations({ workspace, onNavigate }: Props) {
  const { t } = useTranslation()
  const label = t(`workspaces.${workspace.id}.label`)
  return (
    <nav aria-label={t('shell.sections', { workspace: label })}>
      <h2 className="px-sm pb-sm text-metadata font-medium text-secondary-ink">{label}</h2>
      <ul className="flex flex-col gap-[2px]">
        {workspace.destinations.map((destination) => (
          <li key={destination.id}>
            <NavLink
              to={destination.path}
              end
              onClick={onNavigate}
              className={({ isActive }) =>
                'flex h-control items-center rounded-control px-sm text-body transition-colors duration-state pointer-coarse:h-touch ' +
                (isActive
                  ? 'bg-planning-thread-soft font-medium text-planning-thread shadow-[inset_1px_0_0_theme(colors.planning-thread)]'
                  : 'text-secondary-ink hover:bg-canvas-mist hover:text-primary-ink')
              }
            >
              {t(`${destinationKey(workspace, destination)}.label`)}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}
