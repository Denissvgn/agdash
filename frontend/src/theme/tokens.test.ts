import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import resolveConfig from 'tailwindcss/resolveConfig'
import { describe, expect, test } from 'vitest'
import { designTokens, parseDesignFrontmatter } from '../../scripts/design-tokens.mjs'
import tailwindConfig from '../../tailwind.config.js'
import figures from './agdash-figures.json'
import prose from './workchord-prose.json'
import tokens from './workchord-tokens.json'

// WorkChord's DESIGN.md when it is checked out next to this repository, or where
// WORKCHORD_DESIGN_MD points. CI has neither, and skips the comparison.
const DESIGN_MD = process.env.WORKCHORD_DESIGN_MD ?? resolve(__dirname, '../../../../WorkChord/DESIGN.md')

const theme = resolveConfig(tailwindConfig).theme as unknown as Record<string, Record<string, unknown>>
const withoutAlpha = (value: unknown) => String(value).replace(' / <alpha-value>', '')

describe('the tokens are WorkChord DESIGN.md', () => {
  test.skipIf(!existsSync(DESIGN_MD))('the token file equals the frontmatter of DESIGN.md', () => {
    expect(tokens).toEqual(designTokens(readFileSync(DESIGN_MD, 'utf8')))
  })

  test.skipIf(!existsSync(DESIGN_MD))('every prose value is stated in DESIGN.md', () => {
    const text = readFileSync(DESIGN_MD, 'utf8')
    for (const value of Object.values(prose.shadows).slice(1)) expect(text).toContain(value)
    for (const value of ['52px', '220px', '1240px', '1180px', '520px', '44px', '38px', '30px']) {
      expect(text).toContain(value)
    }
  })

  test('the token file has every group and the full palette', () => {
    expect(Object.keys(tokens)).toEqual(['colors', 'typography', 'rounded', 'spacing', 'components'])
    expect(Object.keys(tokens.colors)).toHaveLength(16)
    for (const value of Object.values(tokens.colors)) expect(value).toMatch(/^rgb\(\d+ \d+ \d+\)$/)
  })
})

describe('the styles are built from the tokens', () => {
  test('colours: exactly the palette, the cited derived roles and the figure layer, nothing else', () => {
    const derived = [
      ...Object.keys(prose.derived.colors),
      ...[...Object.keys(figures.ink.light.steps), ...Object.keys(figures.roles)].map((name) => `figure-${name}`),
    ]
    const keywords = ['transparent', 'current', 'inherit']
    expect(Object.keys(theme.colors).sort()).toEqual([...keywords, ...Object.keys(tokens.colors), ...derived].sort())
    const palette = Object.fromEntries(
      Object.entries(theme.colors).filter(([name]) => !keywords.includes(name) && !derived.includes(name)),
    )
    expect(Object.fromEntries(Object.entries(palette).map(([k, v]) => [k, withoutAlpha(v)]))).toEqual(tokens.colors)
  })

  test('type scale', () => {
    for (const [name, style] of Object.entries(tokens.typography)) {
      const [size, options] = theme.fontSize[name] as [string, Record<string, string>]
      expect(size).toBe(style.fontSize)
      expect(options.lineHeight).toBe(String(style.lineHeight))
      expect(options.fontWeight).toBe(String(style.fontWeight))
      if ('letterSpacing' in style) expect(options.letterSpacing).toBe(style.letterSpacing)
    }
    expect(theme.fontFamily.sans).toEqual(['Inter Variable', 'Inter', 'system-ui', 'sans-serif'])
  })

  test('radii, spacing, shadows and layout', () => {
    for (const [name, value] of Object.entries(tokens.rounded)) expect(theme.borderRadius[name]).toBe(value)
    for (const [name, value] of Object.entries(tokens.spacing)) expect(theme.spacing[name]).toBe(value)
    expect(theme.spacing.control).toBe(tokens.components['button-primary'].height)
    expect(theme.spacing['workspace-item']).toBe(tokens.components['workspace-active'].height)
    expect(theme.spacing.conductor).toBe('52px')
    expect(theme.spacing.sidebar).toBe('220px')
    expect(theme.maxWidth.page).toBe('1240px')
    expect(theme.boxShadow.resting).toBe(prose.shadows.resting)
    expect(theme.boxShadow.popover).toBe(prose.shadows.popover)
    expect(theme.screens).toEqual({ roomy: '521px', wide: '1181px' })
  })
})

describe('reading the frontmatter', () => {
  const sample = [
    '---',
    'name: Example',
    'colors:',
    '  ink: "rgb(1 2 3)"',
    'typography:',
    '  body:',
    '    fontSize: "1rem"',
    '    fontWeight: 400',
    '    lineHeight: 1.5',
    '---',
    '# Body',
  ].join('\n')

  test('nested maps, quoted and plain strings, numbers', () => {
    expect(parseDesignFrontmatter(sample)).toEqual({
      name: 'Example',
      colors: { ink: 'rgb(1 2 3)' },
      typography: { body: { fontSize: '1rem', fontWeight: 400, lineHeight: 1.5 } },
    })
  })

  test('anything outside the subset is refused, not guessed', () => {
    expect(() => parseDesignFrontmatter('# no frontmatter')).toThrow('no frontmatter')
    expect(() => parseDesignFrontmatter('---\n  - a list item\n---\n')).toThrow('unsupported')
    expect(() => parseDesignFrontmatter('---\nkey: [1, 2]\n---\n')).toThrow('unsupported')
  })
})

describe('derived values are cited token colours', () => {
  test('each derived colour is its named DESIGN.md colour at a fixed opacity, with the gap it fills', () => {
    const colours = tokens.colors as Record<string, string>
    for (const [name, role] of Object.entries(prose.derived.colors)) {
      const base = colours[role.base]
      expect(base, `${name} names a token`).toBeDefined()
      const [, channels] = base.match(/^rgb\((\d+ \d+ \d+)\)$/) ?? []
      expect(role.value, name).toMatch(new RegExp(`^rgb\\(${channels} / 0?\\.\\d+\\)$`))
      expect(role.fills.length, `${name} cites what it fills`).toBeGreaterThan(20)
      expect(theme.colors[name]).toBe(role.value)
    }
  })
})
