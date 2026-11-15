// Read the design tokens from the frontmatter of WorkChord's DESIGN.md.
//
//   node scripts/design-tokens.mjs <path to WorkChord/DESIGN.md>
//
// rewrites src/theme/workchord-tokens.json. The frontmatter uses a small YAML
// subset (nested maps, quoted and plain strings, numbers), which this parser covers;
// anything else is refused rather than guessed.

import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ENTRY = /^( *)([A-Za-z0-9-]+):(?: (.*))?$/

function scalar(raw) {
  if (/^".*"$/.test(raw)) return raw.slice(1, -1)
  if (/^-?\d+(\.\d+)?$/.test(raw)) return Number(raw)
  if (/^[^"'{}[\]#&*!|>%@`]/.test(raw)) return raw.trim() // a plain YAML string
  throw new Error(`unsupported frontmatter value: ${raw}`)
}

export function parseDesignFrontmatter(text) {
  const match = text.match(/^---\n([\s\S]*?)\n---\n/)
  if (!match) throw new Error('DESIGN.md has no frontmatter')
  const root = {}
  const stack = [{ indent: -1, node: root }]
  for (const line of match[1].split('\n')) {
    if (!line.trim()) continue
    const entry = line.match(ENTRY)
    if (!entry) throw new Error(`unsupported frontmatter line: ${line}`)
    const [, spaces, key, raw] = entry
    const indent = spaces.length
    while (stack.at(-1).indent >= indent) stack.pop()
    const parent = stack.at(-1).node
    if (raw === undefined || raw === '') {
      parent[key] = {}
      stack.push({ indent, node: parent[key] })
    } else {
      parent[key] = scalar(raw)
    }
  }
  return root
}

export function designTokens(text) {
  const { colors, typography, rounded, spacing, components } = parseDesignFrontmatter(text)
  return { colors, typography, rounded, spacing, components }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const source = process.argv[2]
  if (!source) {
    console.error('usage: node scripts/design-tokens.mjs <path to WorkChord/DESIGN.md>')
    process.exit(2)
  }
  const target = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'theme', 'workchord-tokens.json')
  writeFileSync(target, JSON.stringify(designTokens(readFileSync(source, 'utf8')), null, 2) + '\n')
  console.log(`wrote ${target}`)
}
