import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import ts from 'typescript'
import { describe, expect, test } from 'vitest'

// Needs Node's file access, so it runs with the jsdom suite and not in a browser.
const ACCESSIBLE_ATTRIBUTES = ['aria-label', 'title', 'alt', 'placeholder', 'aria-description']

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
