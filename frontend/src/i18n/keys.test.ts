import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import ts from 'typescript'
import { describe, expect, test } from 'vitest'
import { catalogue } from '.'

// Every catalogue string is used, and every key the code names exists.
//
// A key counts as used when the source names it:
// * as a string anywhere, such as t('history.list.more') or titleKey="…";
// * through a section it belongs to, written as a string, such as scope="workchord.error"
//   or a page's base key, which the code completes with a known ending;
// * through the fixed start of a built key, such as t(`history.kinds.${kind}`), which
//   covers the family the value chooses from;
// * as the base of a plural, which i18next completes with _one or _other.

type Tree = { [key: string]: string | Tree }
const SOURCE = join(__dirname, '..')
const PLURAL = /_(zero|one|two|few|many|other)$/

function leaves(tree: Tree, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([key, value]) =>
    typeof value === 'string' ? [`${prefix}${key}`] : leaves(value, `${prefix}${key}.`),
  )
}

const LEAVES = leaves(catalogue as Tree)
const LEAF_SET = new Set(LEAVES)
// Every path that holds keys, such as "history" and "history.list".
const SECTIONS = new Set(
  LEAVES.flatMap((leaf) => {
    const parts = leaf.split('.')
    return parts.slice(1).map((_, i) => parts.slice(0, i + 1).join('.'))
  }),
)
const BASES = new Set(LEAVES.filter((leaf) => PLURAL.test(leaf)).map((leaf) => leaf.replace(PLURAL, '')))
const exists = (key: string) => LEAF_SET.has(key) || BASES.has(key)
// Navigation builds these from its own tables; the catalogue test checks each one there.
const BUILT_ELSEWHERE = ['workspaces.']

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name)
    if (statSync(path).isDirectory()) return name === 'test' ? [] : sourceFiles(path)
    return /\.tsx?$/.test(path) && !/\.test\.tsx?$/.test(path) ? [path] : []
  })
}

type Found = { literals: Set<string>; prefixes: Set<string>; called: { key: string; where: string }[] }

function scan(): Found {
  const found: Found = { literals: new Set(), prefixes: new Set(), called: [] }
  for (const path of sourceFiles(SOURCE)) {
    const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
    const file = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, kind)
    const where = (node: ts.Node) =>
      `${relative(SOURCE, path)}:${file.getLineAndCharacterOfPosition(node.getStart()).line + 1}`
    const visit = (node: ts.Node) => {
      if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
        found.literals.add(node.text)
        // Literal keys handed to the translator, or to a prop or variable named …Key.
        const parent = node.parent
        const callee = parent && ts.isCallExpression(parent) && parent.arguments[0] === node ? parent.expression.getText() : null
        const named =
          parent &&
          ((ts.isJsxAttribute(parent) && /Key$/.test(parent.name.getText())) ||
            (ts.isPropertyAssignment(parent) && /Key$/.test(parent.name.getText())))
        if (callee === 't' || callee === 'i18n.t' || named) found.called.push({ key: node.text, where: where(node) })
      }
      if (ts.isTemplateExpression(node) && node.head.text.includes('.')) {
        const callee = ts.isCallExpression(node.parent) ? node.parent.expression.getText() : null
        if (callee === 't' || callee === 'i18n.t') found.prefixes.add(node.head.text)
      }
      ts.forEachChild(node, visit)
    }
    visit(file)
  }
  return found
}

const FOUND = scan()

function used(leaf: string): boolean {
  const base = leaf.replace(PLURAL, '')
  if (FOUND.literals.has(leaf) || FOUND.literals.has(base)) return true
  // A section counts from two parts on: a bare word such as 'history' is also an id.
  const parts = leaf.split('.')
  for (let i = 2; i < parts.length; i += 1) {
    if (FOUND.literals.has(parts.slice(0, i).join('.'))) return true
  }
  return [...FOUND.prefixes, ...BUILT_ELSEWHERE].some((prefix) => leaf.startsWith(prefix))
}

describe('the catalogue and the code agree', () => {
  test('every catalogue string is used', () => {
    expect(LEAVES.length).toBeGreaterThan(100)
    expect(LEAVES.filter((leaf) => !used(leaf))).toEqual([])
  })

  test('every key the code names exists', () => {
    expect(FOUND.called.length).toBeGreaterThan(50)
    expect(FOUND.called.filter(({ key }) => !exists(key) && !SECTIONS.has(key)).map(({ key, where }) => `${where} ${key}`)).toEqual([])
  })

  test('every built key starts inside the catalogue', () => {
    const missing = [...FOUND.prefixes].filter((prefix) => !SECTIONS.has(prefix.replace(/\.$/, '')))
    expect(missing).toEqual([])
  })

  test('the check finds what it looks for', () => {
    expect(used('history.list.more')).toBe(true)
    expect(used('history.kinds.thinking')).toBe(true) // through t(`history.kinds.${…}`)
    expect(used('workchord.error.retry')).toBe(true) // through scope="workchord.error"
    expect(used('session.all_other')).toBe(true) // a plural of session.all
    expect(used('no.such.key')).toBe(false)
    // A workspace id names no section of keys.
    expect(FOUND.literals.has('history')).toBe(true)
    expect(used('history.unused.example')).toBe(false)
    expect(exists('session.all')).toBe(true)
    expect(exists('history.list.nope')).toBe(false)
  })
})
