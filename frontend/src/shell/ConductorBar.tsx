import { Eye, Menu } from 'lucide-react'
import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, NavLink } from 'react-router-dom'
import { Mark } from './Mark'
import { HOME_PATH, WORKSPACES } from './navigation'

export const DRAWER_ID = 'navigation-drawer'

interface Props {
  drawerOpen: boolean
  onOpenMenu: () => void
  menuButtonRef: RefObject<HTMLButtonElement | null>
}

const workspaceItem =
  'inline-flex h-workspace-item items-center gap-sm rounded-control px-[10px] text-body ' +
  'transition-colors duration-state'

/** The global workspace layer: wordmark, workspace switcher and the read-only marker. */
export function ConductorBar({ drawerOpen, onOpenMenu, menuButtonRef }: Props) {
  const { t } = useTranslation()
  return (
    <header
      className={
        'sticky top-0 z-30 flex h-[calc(theme(spacing.conductor)+env(safe-area-inset-top))] items-center gap-md ' +
        'bg-conductor-navy pt-[env(safe-area-inset-top)] text-working-paper ' +
        'pl-[max(theme(spacing.md),env(safe-area-inset-left))] pr-[max(theme(spacing.md),env(safe-area-inset-right))] ' +
        'wide:pl-[max(theme(spacing.lg),env(safe-area-inset-left))] wide:pr-[max(theme(spacing.lg),env(safe-area-inset-right))]'
      }
    >
      <button
        ref={menuButtonRef}
        type="button"
        onClick={onOpenMenu}
        aria-haspopup="dialog"
        aria-expanded={drawerOpen}
        aria-controls={DRAWER_ID}
        className="inline-flex h-control items-center gap-sm rounded-control px-[10px] text-body text-inverse-soft transition-colors duration-state hover:bg-conductor-panel hover:text-working-paper pointer-coarse:h-touch wide:hidden"
      >
        <Menu aria-hidden="true" size={18} strokeWidth={1.75} />
        <span>{t('shell.openMenu')}</span>
      </button>

      <Link
        to={HOME_PATH}
        aria-label={t('app.home')}
        className="inline-flex items-center gap-sm rounded-compact text-title text-working-paper"
      >
        <Mark />
        {t('app.name')}
      </Link>

      <nav aria-label={t('shell.workspaces')} className="ml-md hidden h-full wide:block">
        <ul className="flex h-full items-center gap-xs">
          {WORKSPACES.map((workspace) => {
            const Icon = workspace.icon
            return (
              <li key={workspace.id}>
                <NavLink
                  to={workspace.path}
                  className={({ isActive }) =>
                    `${workspaceItem} ${
                      isActive
                        ? 'bg-conductor-panel text-working-paper shadow-[inset_0_-2px_0_theme(colors.planning-thread)]'
                        : 'text-inverse-muted hover:bg-conductor-hover hover:text-working-paper'
                    }`
                  }
                >
                  <Icon aria-hidden="true" size={16} strokeWidth={1.75} />
                  {t(`workspaces.${workspace.id}.label`)}
                </NavLink>
              </li>
            )
          })}
        </ul>
      </nav>

      <p
        className="ml-auto inline-flex items-center gap-xs rounded-full border border-inverse-rule px-[10px] py-[3px] text-label text-inverse-soft"
        title={t('shell.readOnlyDetail')}
      >
        <Eye aria-hidden="true" size={14} strokeWidth={1.75} />
        <span>{t('shell.readOnly')}</span>
        <span className="sr-only">{t('shell.readOnlyDetail')}</span>
      </p>
    </header>
  )
}
