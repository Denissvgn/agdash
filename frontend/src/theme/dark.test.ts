import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import postcss, { type Rule } from 'postcss'
import tailwindcss from 'tailwindcss'
import { beforeAll, describe, expect, test } from 'vitest'
import { ROLES, SHADOWS, darkTokens } from '../../scripts/dark-tokens.mjs'
import tailwindConfig from '../../tailwind.config.js'
import figures from './agdash-figures.json'
import dark from './workchord-dark.json'
import prose from './workchord-prose.json'
import tokens from './workchord-tokens.json'

// WorkChord's frontend when it is checked out next to this repository, or where
// WORKCHORD_FRONTEND points. CI has neither, and skips the comparison.
const FRONTEND = process.env.WORKCHORD_FRONTEND ?? resolve(__dirname, '../../../../WorkChord/frontend')
const SOURCES = ['src/styles/globals.css', 'src/styles/planning-masters.css'].map((path) => resolve(FRONTEND, path))

type Name = keyof typeof tokens.colors
const names = Object.keys(tokens.colors) as Name[]
const lightValue = (name: Name) => tokens.colors[name]
const darkValue = (name: Name) => dark.colors[name].value

// WCAG 2 relative luminance and contrast ratio.
const channels = (value: string) => (value.match(/\d+/g) ?? []).slice(0, 3).map(Number)
const luminance = (value: string) => {
  const [r, g, b] = channels(value).map((channel) => {
    const part = channel / 255
    return part <= 0.04045 ? part / 12.92 : ((part + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a: string, b: string) => {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (high + 0.05) / (low + 0.05)
}
/** A colour at an opacity over a surface, as a browser composites it. */
const over = (value: string, opacity: number, surface: string) => {
  const back = channels(surface)
  return `rgb(${channels(value)
    .map((channel, index) => Math.round(opacity * channel + (1 - opacity) * back[index]))
    .join(' ')})`
}

describe("the dark values are WorkChord's dark theme", () => {
  test.skipIf(!SOURCES.every((path) => existsSync(path)))(
    "the dark file is what the script reads from WorkChord's stylesheets",
    () => {
      const stylesheets = SOURCES.map((path) => readFileSync(path, 'utf8'))
      expect(dark).toEqual(darkTokens(stylesheets, tokens, prose))
    },
  )

  test('every token has a dark value, from the WorkChord role that carries it', () => {
    expect(Object.keys(dark.colors)).toEqual(names)
    for (const name of names) {
      expect(dark.colors[name].role, name).toBe(`--${ROLES[name]}`)
      expect(darkValue(name), name).toMatch(/^rgb\(\d+ \d+ \d+\)$/)
    }
  })

  test('every shadow has a dark value: the same shape, on black and stronger', () => {
    expect(Object.keys(dark.shadows)).toEqual(Object.keys(SHADOWS))
    const shape = (value: string) => value.replace(/rgba\([^)]*\)/g, 'colour')
    const strengths = (value: string) => [...value.matchAll(/,(\.\d+)\)/g)].map((match) => Number(match[1]))
    for (const name of Object.keys(SHADOWS) as (keyof typeof SHADOWS)[]) {
      const [lightShadow, darkShadow] = [prose.shadows[name], dark.shadows[name].value]
      expect(shape(darkShadow), name).toBe(shape(lightShadow))
      strengths(darkShadow).forEach((strength, index) => expect(strength).toBeGreaterThan(strengths(lightShadow)[index]))
    }
  })

  test('the conductor bar is one navy in both themes; every surface and ink changes', () => {
    const constant = names.filter((name) => darkValue(name) === lightValue(name))
    expect(constant).toEqual(['conductor-navy', 'conductor-panel', 'strong-rule'])
  })
})

describe('the styles carry both themes', () => {
  const blocks: Record<string, Record<string, string>> = {}

  beforeAll(async () => {
    const built = await postcss([tailwindcss({ ...tailwindConfig, content: [{ raw: '' }] })]).process('@tailwind base;', {
      from: undefined,
    })
    built.root.walkRules((rule: Rule) => {
      const parent = rule.parent?.type === 'atrule' ? `${(rule.parent as postcss.AtRule).params} ` : ''
      const declared: Record<string, string> = {}
      rule.walkDecls((declaration) => {
        declared[declaration.prop] = declaration.value
      })
      if ('--color-working-paper' in declared) blocks[`${parent}${rule.selector}`] = declared
    })
  })

  const expected = (mode: 'light' | 'dark') => ({
    'color-scheme': mode,
    ...Object.fromEntries(
      names.map((name) => [`--color-${name}`, channels(mode === 'dark' ? darkValue(name) : lightValue(name)).join(' ')]),
    ),
    ...Object.fromEntries(
      (Object.keys(SHADOWS) as (keyof typeof SHADOWS)[]).map((name) => [
        `--shadow-${name}`,
        mode === 'dark' ? dark.shadows[name].value : prose.shadows[name],
      ]),
    ),
    ...Object.fromEntries(
      Object.entries(figures.ink[mode].steps).map(([step, value]) => [`--figure-${step}`, channels(value).join(' ')]),
    ),
  })

  test('light by default, dark when the system asks for it, and either when data-theme pins it', () => {
    expect(Object.keys(blocks)).toEqual([
      ':root, [data-theme="light"]',
      '(prefers-color-scheme: dark) :root:not([data-theme="light"])',
      '[data-theme="dark"]',
    ])
    expect(blocks[':root, [data-theme="light"]']).toMatchObject(expected('light'))
    expect(blocks['(prefers-color-scheme: dark) :root:not([data-theme="light"])']).toMatchObject(expected('dark'))
    expect(blocks['[data-theme="dark"]']).toMatchObject(expected('dark'))
  })

  test('the two dark blocks are the same, so a pinned theme looks like the system one', () => {
    expect(blocks['[data-theme="dark"]']).toEqual(blocks['(prefers-color-scheme: dark) :root:not([data-theme="light"])'])
  })
})

describe('a disabled primary action keeps its disabled look under the pointer', () => {
  const rules: { selector: string; declared: Record<string, string> }[] = []

  beforeAll(async () => {
    const source = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
    // The button classes are kept only where the content uses them.
    const content = [{ raw: '<button class="button-primary button-secondary"></button>' }]
    const built = await postcss([tailwindcss({ ...tailwindConfig, content })]).process(source, { from: undefined })
    built.root.walkRules((rule: Rule) => {
      const declared: Record<string, string> = {}
      rule.walkDecls((declaration) => {
        declared[declaration.prop] = declaration.value
      })
      for (const selector of rule.selectors) rules.push({ selector, declared })
    })
  })

  // Classes, attributes and pseudo-classes, counting the most specific part of :not() and :is().
  const weight = (selector: string) => (selector.match(/\.[\w-]+|\[[^\]]+\]|:(?!not\(|is\()[\w-]+/g) ?? []).length

  test('no hover fill outranks the disabled rule unless it leaves disabled buttons out', () => {
    for (const button of ['.button-primary', '.button-secondary']) {
      const disabled = rules.find((rule) => rule.selector === `${button}:disabled`)
      expect(disabled?.declared['background-color'], button).toBeDefined()
      const hovers = rules.filter(
        (rule) => rule.selector.startsWith(button) && rule.selector.includes(':hover') && 'background-color' in rule.declared,
      )
      expect(hovers.length, `${button} has hover fills`).toBeGreaterThan(0)
      for (const hover of hovers) {
        const spares = hover.selector.includes(':not(:disabled)')
        expect(spares || weight(hover.selector) <= weight(disabled!.selector), hover.selector).toBe(true)
      }
    }
  })

  test('the dark hover of the primary action is one of those, in both ways the theme is chosen', () => {
    const dark = rules.filter((rule) => rule.selector.startsWith('.button-primary') && rule.selector.includes('data-theme'))
    expect(dark.map((rule) => rule.selector.replace('.button-primary:hover:not(:disabled)', '')).sort()).toEqual([
      ':is([data-theme="dark"] *)',
      ':not([data-theme="light"] *)',
    ])
  })
})

describe('the dark theme keeps text readable (WCAG AA)', () => {
  const surfaces: Name[] = ['canvas-mist', 'working-paper']
  const AA = 4.5

  test('ink on every surface', () => {
    for (const surface of surfaces) {
      for (const ink of ['primary-ink', 'secondary-ink', 'planning-thread', 'status-planned'] as Name[]) {
        expect(contrast(darkValue(ink), darkValue(surface)), `${ink} on ${surface}`).toBeGreaterThanOrEqual(AA)
      }
    }
  })

  test('a selected row or pressed pill: the planning thread and primary ink on its soft tint', () => {
    for (const ink of ['planning-thread', 'primary-ink'] as Name[]) {
      expect(contrast(darkValue(ink), darkValue('planning-thread-soft')), ink).toBeGreaterThanOrEqual(AA)
    }
  })

  test('the primary action: working paper on primary ink, and on its hover fill', () => {
    for (const fill of ['primary-ink', 'secondary-ink'] as Name[]) {
      expect(contrast(darkValue('working-paper'), darkValue(fill)), fill).toBeGreaterThanOrEqual(AA)
    }
  })

  test('the conductor bar: inverse ink, soft and muted, on the navy and its panel', () => {
    const white = lightValue('working-paper')
    for (const fill of ['conductor-navy', 'conductor-panel'] as Name[]) {
      // The weakest inverse text is the muted role, working paper at 75%.
      expect(contrast(over(white, 0.75, darkValue(fill)), darkValue(fill)), fill).toBeGreaterThanOrEqual(AA)
    }
  })

  test('notice icons and state marks stand at least 3:1 from their surface', () => {
    for (const surface of surfaces) {
      for (const mark of ['feedback-warning', 'feedback-danger', 'status-active', 'status-resolved', 'status-closed'] as Name[]) {
        expect(contrast(darkValue(mark), darkValue(surface)), `${mark} on ${surface}`).toBeGreaterThanOrEqual(3)
      }
      // A field's border is the strong rule, which is the control's only edge.
      expect(contrast(darkValue('strong-rule'), darkValue(surface)), `strong-rule on ${surface}`).toBeGreaterThanOrEqual(3)
    }
  })
})

describe('the figure roles hold on the dark surface', () => {
  const surface = darkValue('working-paper')
  const step = (name: string) => figures.ink.dark.steps[name as keyof typeof figures.ink.dark.steps]

  test('the dark ramp is drawn over the dark working paper, beside the dark planning thread', () => {
    expect(figures.ink.dark.over).toBe(surface)
    expect(figures.cobalt.dark).toBe(darkValue('planning-thread'))
  })

  test('the mark and its hover stand at least 3:1 from the surface; the context ink stays behind them', () => {
    expect(contrast(step(figures.roles.mark), surface)).toBeGreaterThanOrEqual(3)
    expect(contrast(step(figures.roles['mark-hover']), surface)).toBeGreaterThanOrEqual(3)
    expect(contrast(step(figures.roles.context), surface)).toBeLessThan(contrast(step(figures.roles.mark), surface))
  })

  test('the lightest step that carries a mark clears the surface (2:1), as on working paper', () => {
    expect(contrast(step('ink-4'), surface)).toBeGreaterThanOrEqual(2)
    expect(contrast(figures.ink.light.steps['ink-4'], lightValue('working-paper'))).toBeGreaterThanOrEqual(2)
  })

  test('figure text and furniture: axis labels and values in ink, the baseline in strong rule', () => {
    for (const ink of ['primary-ink', 'secondary-ink'] as Name[]) expect(contrast(darkValue(ink), surface)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(darkValue('strong-rule'), surface)).toBeGreaterThanOrEqual(3)
  })

  test('the selection window: marks inside it stay 3:1 from the soft tint behind them, in both themes', () => {
    expect(contrast(step(figures.roles.mark), darkValue('planning-thread-soft'))).toBeGreaterThanOrEqual(3)
    const lightMark = figures.ink.light.steps[figures.roles.mark as keyof typeof figures.ink.light.steps]
    expect(contrast(lightMark, lightValue('planning-thread-soft'))).toBeGreaterThanOrEqual(3)
    expect(contrast(lightMark, lightValue('working-paper'))).toBeGreaterThanOrEqual(3)
  })
})
