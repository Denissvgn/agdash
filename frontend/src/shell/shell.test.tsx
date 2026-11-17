import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import { I18nextProvider } from 'react-i18next'
import App from '../App'
import { catalogue, createI18n, LANGUAGE } from '../i18n'
import { DestinationPage } from '../pages/DestinationPage'
import { unnamedControls } from '../test/controls'
import { DESKTOP, PHONE, setWidth } from '../test/viewport'
import { WORKSPACES } from './navigation'

function open(path: string) {
  window.location.hash = `#${path}`
  return render(<App />)
}

beforeEach(() => {
  window.location.hash = ''
})

describe('the conductor bar', () => {
  test('switches between the five workspaces in order', () => {
    open('/live')
    const nav = screen.getByRole('navigation', { name: 'Workspaces' })
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Live',
      'History',
      'Projects',
      'Analytics',
      'Settings',
    ])
  })

  test('marks the current workspace, including on its inner pages', () => {
    open('/history/search')
    const nav = screen.getByRole('navigation', { name: 'Workspaces' })
    expect(within(nav).getByRole('link', { name: 'History' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Live' })).not.toHaveAttribute('aria-current')
  })

  test('says that agdash only observes', () => {
    open('/live')
    expect(screen.getByText('Read-only')).toBeInTheDocument()
    expect(screen.getByText('agdash observes your agents and never changes them.')).toBeInTheDocument()
  })

  test('the wordmark leads to the live board', () => {
    open('/settings/data')
    fireEvent.click(screen.getByRole('link', { name: 'agdash, go to the live board' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Live board' })).toBeInTheDocument()
  })
})

describe('pages', () => {
  test('the root opens the live board', () => {
    open('/')
    expect(screen.getByRole('heading', { level: 1, name: 'Live board' })).toBeInTheDocument()
  })

  test('the sidebar lists the current workspace and marks the current destination', () => {
    open('/history/search')
    const sections = screen.getByRole('navigation', { name: 'History sections' })
    expect(within(sections).getAllByRole('link').map((link) => link.textContent)).toEqual(['Sessions', 'Search'])
    expect(within(sections).getByRole('link', { name: 'Search' })).toHaveAttribute('aria-current', 'page')
    expect(within(sections).getByRole('link', { name: 'Sessions' })).not.toHaveAttribute('aria-current')
    expect(screen.getByRole('heading', { level: 1, name: 'Search' })).toBeInTheDocument()
  })

  test('a view without its data says what it will show and what it waits on', () => {
    // Every destination has its page now; one added before its data exists gets this panel.
    const workspace = WORKSPACES.find((found) => found.id === 'settings')!
    const waiting = createI18n({
      [LANGUAGE]: {
        translation: {
          ...catalogue,
          workspaces: {
            settings: {
              destinations: {
                sample: { title: 'Sample', purpose: 'A view to come.', what: 'Sample rows.', waitsOn: 'The agdash service.' },
              },
            },
          },
        },
      },
    })
    render(
      <I18nextProvider i18n={waiting}>
        <DestinationPage destination={{ id: 'sample', path: '/settings/sample' }} workspace={workspace} />
      </I18nextProvider>,
    )
    const panel = screen.getByRole('region', { name: 'Not available yet' })
    expect(within(panel).getByText('What will appear here')).toBeInTheDocument()
    expect(within(panel).getByText('Sample rows.')).toBeInTheDocument()
    expect(within(panel).getByText('The agdash service.')).toBeInTheDocument()
  })

  test('an unknown address says so and offers the way back', () => {
    open('/no-such-page')
    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: /Go to the live board/ }))
    expect(screen.getByRole('heading', { level: 1, name: 'Live board' })).toBeInTheDocument()
  })

  test('a skip link leads to the content', () => {
    open('/live')
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#content')
    expect(document.getElementById('content')?.tagName).toBe('MAIN')
  })
})

describe('the navigation drawer', () => {
  // The drawer is the navigation of narrow windows.
  beforeEach(() => setWidth(PHONE))
  afterEach(() => setWidth(DESKTOP))

  test('opens from the menu button and closes on Escape, returning focus', async () => {
    open('/analytics')
    const button = screen.getByRole('button', { name: 'Menu' })
    expect(button).toHaveAttribute('aria-haspopup', 'dialog')
    expect(button).toHaveAttribute('aria-expanded', 'false')
    await act(async () => fireEvent.click(button))
    const drawer = screen.getByRole('dialog', { name: 'Navigation' })
    expect(drawer).toHaveAttribute('open')
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(button).toHaveAttribute('aria-controls', drawer.id)
    expect(within(drawer).getByRole('navigation', { name: 'Workspaces' })).toBeInTheDocument()
    expect(within(drawer).getByRole('navigation', { name: 'Analytics sections' })).toBeInTheDocument()
    expect(unnamedControls(document.body)).toEqual([])

    // A browser turns Escape on a modal dialog into a cancel event.
    await act(async () => fireEvent(drawer, new Event('cancel', { cancelable: true })))
    expect(drawer).not.toHaveAttribute('open')
    expect(button).toHaveFocus()
  })

  test('closes when a destination is chosen', async () => {
    open('/live')
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Menu' })))
    const drawer = screen.getByRole('dialog', { name: 'Navigation' })
    await act(async () => fireEvent.click(within(drawer).getByRole('link', { name: 'Settings' })))
    expect(drawer).not.toHaveAttribute('open')
    expect(screen.getByRole('heading', { level: 1, name: 'Sources' })).toBeInTheDocument()
  })

  test('the close button closes it', async () => {
    open('/live')
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Menu' })))
    const drawer = screen.getByRole('dialog', { name: 'Navigation' })
    await act(async () => fireEvent.click(within(drawer).getByRole('button', { name: 'Close navigation' })))
    expect(drawer).not.toHaveAttribute('open')
  })

  test('the drawer lists the current workspace\'s destinations', async () => {
    open('/live')
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Menu' })))
    const drawer = screen.getByRole('dialog', { name: 'Navigation' })
    const sections = within(drawer).getByRole('navigation', { name: 'Live sections' })
    expect(within(sections).getAllByRole('link').map((link) => link.textContent)).toEqual(['Board', 'Agent graph'])
  })
})

test('tapping the scrim closes the drawer', async () => {
  await setWidth(PHONE)
  open('/live')
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Menu' })))
  const drawer = screen.getByRole('dialog', { name: 'Navigation' })
  const scrim = drawer.querySelector('button[aria-hidden="true"]')
  expect(scrim).not.toBeNull()
  await act(async () => fireEvent.click(scrim!))
  expect(drawer).not.toHaveAttribute('open')
})

test('the window title names the page', () => {
  open('/history/search')
  expect(document.title).toBe('Search \u00b7 agdash')
})

test('the window title names a missing page too', () => {
  open('/no-such-page')
  expect(document.title).toBe('Page not found \u00b7 agdash')
})

test('the close button is the first thing the drawer can focus', async () => {
  await setWidth(PHONE)
  open('/live')
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Menu' })))
  const drawer = screen.getByRole('dialog', { name: 'Navigation' })
  const focusable = drawer.querySelectorAll('button, a[href], [tabindex]')
  expect(focusable[0]).toHaveAccessibleName('Close navigation')
})

afterEach(() => setWidth(DESKTOP))
