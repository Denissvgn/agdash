import { X } from 'lucide-react'
import { useEffect, useRef, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router-dom'
import { DRAWER_ID } from './ConductorBar'
import { Destinations } from './Destinations'
import { WORKSPACES, type Workspace } from './navigation'

interface Props {
  open: boolean
  onClose: () => void
  workspace: Workspace | undefined
  returnFocusTo: RefObject<HTMLButtonElement | null>
}

/**
 * Below 1180px the workspaces and the current workspace's destinations move here.
 * A native modal dialog: focus stays inside while it is open, Escape closes it,
 * and focus goes back to the button that opened it. The dialog covers the window;
 * the panel sits on the left and the rest is a scrim that closes it when tapped.
 */
export function NavDrawer({ open, onClose, workspace, returnFocusTo }: Props) {
  const { t } = useTranslation()
  const dialog = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (open && !element.open) {
      element.showModal()
    } else if (!open && element.open) {
      element.close()
      returnFocusTo.current?.focus()
    }
  }, [open, returnFocusTo])

  return (
    <dialog
      ref={dialog}
      id={DRAWER_ID}
      aria-label={t('shell.navigation')}
      // Browsers turn Escape on a modal dialog into "cancel".
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      className="m-0 h-dvh max-h-none w-full max-w-none bg-transparent p-0 backdrop:bg-transparent"
    >
      <div className="relative z-10 flex h-full w-[min(320px,86vw)] flex-col bg-working-paper pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] text-primary-ink shadow-popover animate-[drawer-in_180ms_cubic-bezier(0.16,1,0.3,1)] motion-reduce:animate-none">
        <div className="flex h-[calc(theme(spacing.conductor)+env(safe-area-inset-top))] shrink-0 items-center justify-between border-b border-hairline px-md pt-[env(safe-area-inset-top)]">
          <span className="text-title">{t('app.name')}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('shell.closeMenu')}
            className="inline-flex size-control items-center justify-center rounded-control text-secondary-ink transition-colors duration-state hover:bg-canvas-mist hover:text-primary-ink pointer-coarse:size-touch"
          >
            <X aria-hidden="true" size={18} strokeWidth={1.75} />
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-lg overflow-y-auto p-md">
          <nav aria-label={t('shell.workspaces')}>
            <ul className="flex flex-col gap-[2px]">
              {WORKSPACES.map((item) => {
                const Icon = item.icon
                return (
                  <li key={item.id}>
                    <NavLink
                      to={item.path}
                      onClick={onClose}
                      className={({ isActive }) =>
                        'flex h-control items-center gap-sm rounded-control px-sm text-body transition-colors duration-state pointer-coarse:h-touch ' +
                        (isActive
                          ? 'bg-planning-thread-soft font-medium text-planning-thread'
                          : 'text-primary-ink hover:bg-canvas-mist')
                      }
                    >
                      <Icon aria-hidden="true" size={16} strokeWidth={1.75} />
                      {t(`workspaces.${item.id}.label`)}
                    </NavLink>
                  </li>
                )
              })}
            </ul>
          </nav>
          {workspace && workspace.destinations.length > 1 ? (
            <Destinations workspace={workspace} onNavigate={onClose} />
          ) : null}
        </div>
      </div>
      {/*
        The scrim comes after the panel so the dialog's first focus lands on the close
        button. Keyboard users close with Escape or that button; this is for pointers.
      */}
      <button
        type="button"
        tabIndex={-1}
        aria-hidden="true"
        onClick={onClose}
        className="absolute inset-0 h-full w-full cursor-default bg-scrim"
      />
    </dialog>
  )
}
