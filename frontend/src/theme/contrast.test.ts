import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import ts from 'typescript'
import { describe, expect, test } from 'vitest'
import tokens from './workchord-tokens.json'

// Needs Node's file access, so it runs with the jsdom suite and not in a browser.
const SOURCE = join(__dirname, '..')

type Name = keyof typeof tokens.colors
const value = (name: Name) => tokens.colors[name]

// WCAG 2 relative luminance and contrast ratio.
const luminance = (colour: string) => {
  const [r, g, b] = (colour.match(/\d+/g) ?? []).slice(0, 3).map((channel) => {
    const part = Number(channel) / 255
    return part <= 0.04045 ? part / 12.92 : ((part + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a: string, b: string) => {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (high + 0.05) / (low + 0.05)
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name)
    if (statSync(path).isDirectory()) return name === 'test' ? [] : sourceFiles(path)
    return path.endsWith('.tsx') && !path.endsWith('.test.tsx') ? [path] : []
  })
}

/** Elements that take the given text colour and hold children, so could hold words. */
function colouredContainers(path: string, colour: string): string[] {
  const file = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const found: string[] = []
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) && node.attributes.getText().includes(colour)) {
      const { line } = file.getLineAndCharacterOfPosition(node.getStart())
      found.push(`${relative(SOURCE, path)}:${line + 1} <${node.tagName.getText()}>`)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return found
}

describe('the light theme keeps text readable (WCAG AA)', () => {
  const surfaces: Name[] = ['canvas-mist', 'working-paper']
  const AA = 4.5

  test('ink on every surface', () => {
    for (const surface of surfaces) {
      for (const ink of ['primary-ink', 'secondary-ink', 'planning-thread', 'status-planned'] as Name[]) {
        expect(contrast(value(ink), value(surface)), `${ink} on ${surface}`).toBeGreaterThanOrEqual(AA)
      }
    }
  })

  test('a selected row or pressed pill: the planning thread and primary ink on its soft tint', () => {
    for (const ink of ['planning-thread', 'primary-ink'] as Name[]) {
      expect(contrast(value(ink), value('planning-thread-soft')), ink).toBeGreaterThanOrEqual(AA)
    }
  })

  test('warning amber is enough for an icon or a border and not for words', () => {
    for (const surface of surfaces) {
      const ratio = contrast(value('feedback-warning'), value(surface))
      expect(ratio, surface).toBeGreaterThanOrEqual(3)
      expect(ratio, surface).toBeLessThan(AA)
    }
  })
})

describe('warning amber never colours words', () => {
  test('only an element with no children takes it as its text colour: an icon', () => {
    const holders = sourceFiles(SOURCE).flatMap((path) => colouredContainers(path, 'text-feedback-warning'))
    expect(holders).toEqual([])
  })

  test('the check finds icons that take it, so it is looking at the right thing', () => {
    const icons = sourceFiles(SOURCE).filter((path) => {
      const text = readFileSync(path, 'utf8')
      return text.includes('text-feedback-warning')
    })
    expect(icons.length).toBeGreaterThan(3)
  })
})
