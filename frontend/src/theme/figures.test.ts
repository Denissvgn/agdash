import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { distance, figureColours, oklab } from '../../scripts/figure-colours.mjs'
import figures from './agdash-figures.json'
import dark from './workchord-dark.json'
import tokens from './workchord-tokens.json'

const modes = ['light', 'dark'] as const
const steps = (mode: (typeof modes)[number]) => Object.values(figures.ink[mode].steps)
const lightness = (value: string) => oklab(value)[0]

describe('the figure layer', () => {
  test('is exactly what the script derives from the WorkChord tokens and their dark values', () => {
    expect(figures).toEqual(figureColours(tokens, dark))
  })

  test('keeps every ink step at least ΔE 18 from cobalt, in each mode', () => {
    for (const mode of modes) {
      const cobalt = mode === 'light' ? tokens.colors['planning-thread'] : dark.colors['planning-thread'].value
      for (const value of steps(mode)) expect(distance(value, cobalt)).toBeGreaterThanOrEqual(18)
    }
  })

  test('reads in order away from the surface, with visible steps where marks sit', () => {
    const light = steps('light').map(lightness)
    const dark = steps('dark').map(lightness)
    for (let i = 1; i < 7; i += 1) {
      expect(light[i]).toBeLessThan(light[i - 1])
      expect(dark[i]).toBeGreaterThan(dark[i - 1])
    }
    // Steps 4 to 7 carry marks, so neighbours stay visibly apart (ΔL at least 0.06).
    for (let i = 4; i < 7; i += 1) {
      expect(light[i - 1] - light[i]).toBeGreaterThanOrEqual(0.06)
      expect(dark[i] - dark[i - 1]).toBeGreaterThanOrEqual(0.06)
    }
  })

  test('has one hue: every step is the same navy or paper, only weaker or stronger', () => {
    for (const mode of modes) {
      const hues = steps(mode)
        .slice(2)
        .map((value) => {
          const [, a, b] = oklab(value)
          return (Math.atan2(b, a) * 180) / Math.PI
        })
      expect(Math.max(...hues) - Math.min(...hues)).toBeLessThan(40)
    }
  })

  test("agdash's DESIGN.md lists the same light ink steps", () => {
    const design = readFileSync(resolve(__dirname, '../../../DESIGN.md'), 'utf8')
    const frontmatter = design.split('\n---\n')[0]
    const listed = Object.fromEntries(
      [...frontmatter.matchAll(/^ {2}figure-(ink-\d): "(rgb\(\d+ \d+ \d+\))"$/gm)].map((match) => [match[1], match[2]]),
    )
    expect(listed).toEqual(figures.ink.light.steps)
  })

  test('names roles only by ink step', () => {
    for (const step of Object.values(figures.roles)) expect(Object.keys(figures.ink.light.steps)).toContain(step)
  })
})
