import { describe, expect, test } from 'vitest'
import { BREAK, compressedScale } from './compressed'
import { heatScale, heatStep, niceTop } from './heat'
import { clampRange, shiftRange } from './range'
import { CAP, stateBlock, STATE_FORMS, STRIP_STATES } from './states'
import { TIER_DASH } from './tiers'

const HOUR = 3_600_000
const day = (d: number, h: number) => Date.UTC(2026, 9, 5 + d, h)

describe('the compressed time axis', () => {
  test('keeps work to scale and squeezes each gap over the threshold into one break', () => {
    // Two working stretches a night apart, and a short pause inside the first.
    const x = compressedScale(
      [
        [day(0, 9), day(0, 10)],
        [day(0, 10.25), day(0, 12)],
        [day(1, 9), day(1, 12)],
      ],
      30 * 60_000,
      [0, 618],
    )
    expect(x.gaps).toHaveLength(1)
    expect(x.gaps[0]).toMatchObject({ from: day(0, 12), to: day(1, 9) })
    // The 21-hour night takes the break's width; the two 3-hour stretches share the rest equally.
    const [first, second] = x.stretches
    expect(second.left - first.right).toBe(BREAK)
    expect(first.right - first.left).toBeCloseTo(300)
    expect(second.right - second.left).toBeCloseTo(300)
    expect(x(day(0, 9))).toBe(0)
    expect(x(day(1, 12))).toBeCloseTo(618)
  })

  test('leaves gaps under the threshold in place, and orders every time', () => {
    const times: [number, number][] = [0, 1, 2, 3, 5, 8].map((h) => [day(0, 9 + h), day(0, 9 + h + 0.5)])
    const x = compressedScale(times, 4 * HOUR, [0, 400])
    expect(x.gaps).toHaveLength(0)
    const positions = times.flat().map(x)
    expect([...positions].sort((a, b) => a - b)).toEqual(positions)
  })

  test('gives a lone moment room to show', () => {
    const x = compressedScale(
      [
        [day(0, 9), day(0, 17)],
        [day(2, 9), day(2, 9)],
      ],
      HOUR,
      [0, 500],
    )
    expect(x.stretches[1].right - x.stretches[1].left).toBeGreaterThan(4)
  })
})

describe('the state strips', () => {
  test('tell the five states apart without colour: by height, texture or cap', () => {
    const forms = STRIP_STATES.map((state) => {
      const block = stateBlock(state, 0, 50, 18, 18)
      return JSON.stringify([block.height, block.width, block.form.hatched])
    })
    expect(new Set(forms).size).toBe(5)
    expect(stateBlock('active', 0, 50, 18, 18).height).toBe(18)
    expect(stateBlock('waiting', 0, 50, 18, 18).height).toBe(12)
    expect(stateBlock('idle', 0, 50, 18, 18).height).toBe(6)
    expect(stateBlock('unknown', 0, 50, 18, 18)).toMatchObject({ height: 18, form: { hatched: true } })
    expect(stateBlock('ended', 0, 50, 18, 18).width).toBe(CAP)
  })

  test('use the status colours only: active cyan, ended violet, the rest planned slate', () => {
    expect(Object.fromEntries(STRIP_STATES.map((state) => [state, STATE_FORMS[state].fill]))).toEqual({
      active: 'fill-status-active',
      waiting: 'fill-status-planned',
      idle: 'fill-status-planned',
      unknown: 'fill-status-planned',
      ended: 'fill-status-closed',
    })
  })
})

describe('the heatmap scale', () => {
  test('gives a reported zero the lightest step and bins the rest in six, up to a rounded top', () => {
    const scale = heatScale([0, 1, 20, 21, 113])
    expect(scale.bounds).toEqual([0, 20, 40, 60, 80, 100, 120])
    expect([0, 1, 20, 21, 113].map((value) => heatStep(value, scale))).toEqual(['ink-1', 'ink-2', 'ink-2', 'ink-3', 'ink-7'])
    expect([niceTop(113), niceTop(7), niceTop(0)]).toEqual([120, 12, 0])
  })

  test('gives small whole counts a step each, the largest the darkest', () => {
    const one = heatScale([0, 1, 1])
    expect(one.bounds).toEqual([0, 1])
    expect(heatStep(1, one)).toBe('ink-7')
    const three = heatScale([0, 1, 2, 3])
    expect([0, 1, 2, 3].map((value) => heatStep(value, three))).toEqual(['ink-1', 'ink-5', 'ink-6', 'ink-7'])
  })
})

describe('the evidence tiers', () => {
  test('each draws a different line', () => {
    expect(new Set(Object.values(TIER_DASH)).size).toBe(4)
    expect(TIER_DASH.tool_call).toBeUndefined()
  })
})

describe('the selection range', () => {
  test('stays inside the buckets with its start first', () => {
    expect(clampRange([5, 2], 9)).toEqual([2, 5])
    expect(clampRange([-3, 40], 9)).toEqual([0, 9])
  })

  test('moves whole, keeping its width, and stops at either end', () => {
    expect(shiftRange([2, 5], 3, 9)).toEqual([5, 8])
    expect(shiftRange([2, 5], 10, 9)).toEqual([6, 9])
    expect(shiftRange([2, 5], -10, 9)).toEqual([0, 3])
  })
})
