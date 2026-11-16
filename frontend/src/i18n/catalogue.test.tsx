import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import App from '../App'
import { PAGES } from '../pages/registry'
import { WORKSPACES } from '../shell/navigation'
import { unnamedControls } from '../test/controls'
import { DESKTOP, PHONE, setWidth } from '../test/viewport'
import { catalogue, createI18n, LANGUAGE } from '.'

// A pseudo-locale wraps every catalogue string in markers. Rendered with it, any
// visible text or accessible name without the markers did not come from the
// catalogue.
const OPEN = '⟦'
const CLOSE = '⟧'
type Tree = { [key: string]: string | Tree }
const wrap = (tree: Tree): Tree =>
  Object.fromEntries(
    Object.entries(tree).map(([key, value]) => [key, typeof value === 'string' ? `${OPEN}${value}${CLOSE}` : wrap(value)]),
  )
const pseudo = () => createI18n({ [LANGUAGE]: { translation: wrap(catalogue as Tree) } })

const ACCESSIBLE_ATTRIBUTES = ['aria-label', 'title', 'alt', 'placeholder', 'aria-description']

function strayStrings(root: HTMLElement): string[] {
  const stray: string[] = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent?.trim() ?? ''
    if (text && !(text.startsWith(OPEN) && text.endsWith(CLOSE))) stray.push(`text: ${text}`)
  }
  for (const element of root.querySelectorAll('*')) {
    for (const attribute of ACCESSIBLE_ATTRIBUTES) {
      const value = element.getAttribute(attribute)?.trim()
      if (value && !(value.startsWith(OPEN) && value.endsWith(CLOSE))) stray.push(`${attribute}: ${value}`)
    }
  }
  return stray
}

const ROUTES = [
  ...WORKSPACES.flatMap((workspace) => workspace.destinations.map((destination) => destination.path)),
  '/history/sessions/1',
  '/no-such-page',
]

describe('every visible string comes from the catalogue', () => {
  test.each(ROUTES)('the page at %s', (path) => {
    window.location.hash = `#${path}`
    render(<App i18n={pseudo()} />)
    expect(strayStrings(document.body)).toEqual([])
    expect(unnamedControls(document.body)).toEqual([])
    expect(document.title.startsWith(OPEN) && document.title.endsWith(CLOSE), document.title).toBe(true)
  })

  test('the navigation drawer', async () => {
    await setWidth(PHONE)
    window.location.hash = '#/history'
    render(<App i18n={pseudo()} />)
    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Menu/ })))
    expect(screen.getByRole('dialog')).toHaveAttribute('open')
    expect(strayStrings(document.body)).toEqual([])
    expect(unnamedControls(document.body)).toEqual([])
    await setWidth(DESKTOP)
  })

  test('every workspace and destination has its strings', () => {
    const workspaces = catalogue.workspaces as unknown as Record<string, Tree>
    for (const workspace of WORKSPACES) {
      expect(typeof workspaces[workspace.id]?.label).toBe('string')
      for (const destination of workspace.destinations) {
        const strings = (workspaces[workspace.id].destinations as Tree)[destination.id] as Tree
        // A destination without its own page shows the unavailable panel, which needs two more strings.
        const fields = PAGES[`${workspace.id}:${destination.id}`]
          ? ['label', 'title', 'purpose']
          : ['label', 'title', 'purpose', 'what', 'waitsOn']
        for (const field of fields) {
          expect(typeof strings?.[field], `${workspace.id}.${destination.id}.${field}`).toBe('string')
        }
      }
    }
  })
})
