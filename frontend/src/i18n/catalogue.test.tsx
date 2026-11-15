import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { act, fireEvent, render, screen } from '@testing-library/react'
import ts from 'typescript'
import { describe, expect, test } from 'vitest'
import App from '../App'
import { PAGES } from '../pages/registry'
import { WORKSPACES } from '../shell/navigation'
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
  '/no-such-page',
]

describe('every visible string comes from the catalogue', () => {
  test.each(ROUTES)('the page at %s', (path) => {
    window.location.hash = `#${path}`
    render(<App i18n={pseudo()} />)
    expect(strayStrings(document.body)).toEqual([])
    expect(document.title.startsWith(OPEN) && document.title.endsWith(CLOSE), document.title).toBe(true)
  })

  test('the navigation drawer', async () => {
    window.location.hash = '#/history'
    render(<App i18n={pseudo()} />)
    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Menu/ })))
    expect(screen.getByRole('dialog')).toHaveAttribute('open')
    expect(strayStrings(document.body)).toEqual([])
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

// The rendering check sees what renders; this sees source that a test did not reach.
const SOURCE = join(__dirname, '..')

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name)
    if (statSync(path).isDirectory()) return name === 'test' ? [] : sourceFiles(path)
    return path.endsWith('.tsx') && !path.endsWith('.test.tsx') ? [path] : []
  })
}

function literalStrings(path: string): string[] {
  const file = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const found: string[] = []
  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node) && /\p{L}/u.test(node.text)) found.push(`text "${node.text.trim()}"`)
    if (
      ts.isJsxAttribute(node) &&
      ACCESSIBLE_ATTRIBUTES.includes(node.name.getText()) &&
      node.initializer &&
      ts.isStringLiteral(node.initializer)
    ) {
      found.push(`${node.name.getText()}="${node.initializer.text}"`)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return found.map((entry) => `${relative(SOURCE, path)}: ${entry}`)
}

describe('no component carries a literal string', () => {
  test('source scan', () => {
    const files = sourceFiles(SOURCE)
    expect(files.length).toBeGreaterThan(5)
    expect(files.flatMap(literalStrings)).toEqual([])
  })

  test('the scan finds what it looks for', () => {
    const path = join(SOURCE, 'shell', '__scan_sample__.tsx')
    const file = ts.createSourceFile(
      path,
      'const a = <p title="Hello">Hi there <b>{x}</b></p>',
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    )
    const found: string[] = []
    const visit = (node: ts.Node) => {
      if (ts.isJsxText(node) && /\p{L}/u.test(node.text)) found.push(node.text.trim())
      if (ts.isJsxAttribute(node) && node.initializer && ts.isStringLiteral(node.initializer)) found.push(node.initializer.text)
      ts.forEachChild(node, visit)
    }
    visit(file)
    expect(found).toEqual(['Hello', 'Hi there'])
  })
})
