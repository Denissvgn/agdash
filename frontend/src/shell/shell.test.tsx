import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, test } from 'vitest'
import App from '../App'

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
    open('/projects/unmapped')
    const panel = screen.getByRole('region', { name: 'Not available yet' })
    expect(within(panel).getByText('What will appear here')).toBeInTheDocument()
    expect(within(panel).getByText('A connected WorkChord server.')).toBeInTheDocument()
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

  test('a workspace with one destination shows no section list', async () => {
    open('/live')
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Menu' })))
    const drawer = screen.getByRole('dialog', { name: 'Navigation' })
    expect(within(drawer).queryByRole('navigation', { name: 'Live sections' })).toBeNull()
  })
})

test('tapping the scrim closes the drawer', async () => {
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
  open('/live')
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Menu' })))
  const drawer = screen.getByRole('dialog', { name: 'Navigation' })
  const focusable = drawer.querySelectorAll('button, a[href], [tabindex]')
  expect(focusable[0]).toHaveAccessibleName('Close navigation')
})
