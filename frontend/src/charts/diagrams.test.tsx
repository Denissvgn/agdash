import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { useState, type ReactElement } from 'react'
import { I18nextProvider } from 'react-i18next'
import { describe, expect, test } from 'vitest'
import { catalogue, createI18n, LANGUAGE } from '../i18n'
import { unnamedControls } from '../test/controls'
import { openTwin } from '../test/twin'
import { Heatmap } from './Heatmap'
import { Lanes } from './Lanes'
import type { Range } from './range'
import { SelectionWindow } from './SelectionWindow'
import { daily, heatmap, lanes, strips, agentTree } from './specimen/samples'
import { StateStrips } from './StateStrips'
import { STRIP_STATES } from './states'
import { TIER_DASH } from './tiers'
import { Tree } from './Tree'

const OPEN = '⟦'
const CLOSE = '⟧'
type Tree_ = { [key: string]: string | Tree_ }
const wrap = (tree: Tree_): Tree_ =>
  Object.fromEntries(
    Object.entries(tree).map(([key, value]) => [key, typeof value === 'string' ? `${OPEN}${value}${CLOSE}` : wrap(value)]),
  )

function show(ui: ReactElement, i18n = createI18n()) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>)
}
const plot = (root: HTMLElement) => root.querySelector<HTMLElement>('[role="group"]')!
const active = (group: HTMLElement) => document.getElementById(group.getAttribute('aria-activedescendant') ?? '')
const tooltip = (root: HTMLElement) => root.querySelector('[aria-hidden="true"].shadow-popover')?.textContent ?? null
const press = (group: HTMLElement, key: string) => fireEvent.keyDown(group, { key })
const at = (mark: Element | null) => ({ x: Number(mark?.getAttribute('data-x')), y: Number(mark?.getAttribute('data-y')) })

const LANES = lanes(true)
const FIGURES: [string, () => ReactElement][] = [
  ['time lanes', () => <Lanes lanes={LANES.lanes} now={LANES.now} rowHeader="Agent" title="Work" />],
  ['state strips', () => <StateStrips rowHeader="Session" strips={strips()} title="States" />],
  ['heatmap', () => <Heatmap data={heatmap({ unreported: true })} rowHeader="Weekday" title="Calls" />],
  ['tree', () => <Tree root={agentTree()} rowHeader="Agent" title="Agents" />],
]

describe.each(FIGURES)('%s', (_, figure) => {
  test('is one tab stop; arrows move between marks with the hover tooltip', () => {
    const { container } = show(figure())
    expect([...container.querySelectorAll('[tabindex="0"]')].filter((element) => !element.closest('details'))).toHaveLength(1)
    const group = plot(container)
    act(() => group.focus())
    press(group, 'ArrowDown')
    const mark = active(group)
    expect(mark?.getAttribute('aria-label')).toBeTruthy()
    const keyed = tooltip(container)
    expect(keyed).toBeTruthy()
    press(group, 'Escape')
    act(() => group.blur())
    const box = group.getBoundingClientRect()
    fireEvent.pointerMove(group, { clientX: box.left + at(mark).x, clientY: box.top + at(mark).y })
    expect(tooltip(container)).toBe(keyed)
  })

  test('names every mark from the catalogue, has a table twin, and does not animate', () => {
    const { container } = show(figure(), createI18n({ [LANGUAGE]: { translation: wrap(catalogue as Tree_) } }))
    const marks = [...container.querySelectorAll('[role="img"]')]
    expect(marks.length).toBeGreaterThan(3)
    for (const mark of marks) {
      const name = mark.getAttribute('aria-label') ?? ''
      expect(name.startsWith(OPEN) && name.endsWith(CLOSE), name).toBe(true)
    }
    expect(unnamedControls(container)).toEqual([])
    openTwin(container)
    expect(container.querySelectorAll('table tbody tr').length).toBeGreaterThan(3)
    expect(container.querySelectorAll('animate, animateTransform, animateMotion, set')).toHaveLength(0)
    for (const element of container.querySelectorAll('figure [class]')) {
      expect(element.getAttribute('class')).not.toMatch(/\b(transition|animate-|duration-)/)
    }
  })
})

describe('time lanes', () => {
  test('place spans and moments across days, compress the nights, and mark now in the thread', () => {
    const { container } = show(<Lanes lanes={LANES.lanes} now={LANES.now} rowHeader="Agent" title="Work" />)
    const marks = [...container.querySelectorAll('[role="img"]')]
    const items = LANES.lanes.reduce((sum, lane) => sum + lane.items.length, 0)
    // Two nights and the lunch breaks are compressed; each break is a mark of its own.
    const breaks = marks.filter((mark) => mark.getAttribute('aria-label')?.includes('Idle for'))
    expect(breaks.length).toBeGreaterThanOrEqual(2)
    expect(marks).toHaveLength(items + breaks.length)
    expect(breaks.some((mark) => /\d+ h/.test(mark.getAttribute('aria-label') ?? ''))).toBe(true)
    expect(container.querySelector('line.stroke-planning-thread')).not.toBeNull()
    expect(container.querySelector('text.fill-planning-thread')?.textContent).toBe('Now')
    // The table lists every span and moment, and every break.
    openTwin(container)
    expect(container.querySelectorAll('table tbody tr')).toHaveLength(items + breaks.length)
    // Spans keep time order along each lane.
    const firstLane = marks.filter((mark) => mark.getAttribute('aria-label')?.startsWith('Main agent'))
    const xs = firstLane.map((mark) => at(mark).x)
    expect([...xs].sort((a, b) => a - b)).toEqual(xs)
  })

  test('up and down move to the nearest mark on the next lane', () => {
    const { container } = show(<Lanes lanes={LANES.lanes} now={LANES.now} rowHeader="Agent" title="Work" />)
    const group = plot(container)
    act(() => group.focus())
    const first = active(group)
    press(group, 'ArrowDown')
    const below = active(group)
    expect(below?.getAttribute('aria-label')).toMatch(/^Explorer/)
    expect(Math.abs(at(below).x - at(first).x)).toBeLessThan(120)
  })
})

describe('state strips', () => {
  test("name each state's word in the legend and on every mark", () => {
    const { container } = show(<StateStrips rowHeader="Session" strips={strips()} title="States" />)
    const legend = within(container).getByRole('list', { name: 'Legend' })
    expect(within(legend).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Active',
      'Waiting',
      'Idle',
      'Unknown',
      'Ended',
    ])
    const drawn = new Set<string>()
    for (const mark of container.querySelectorAll('[data-state]')) {
      const state = mark.getAttribute('data-state')!
      drawn.add(state)
      const word = state[0].toUpperCase() + state.slice(1)
      expect(mark.getAttribute('aria-label')).toMatch(new RegExp(`: ${word}$`))
    }
    expect([...drawn].sort()).toEqual([...STRIP_STATES].sort())
  })

  test('draw the forms: heights, the unknown texture and the ended cap', () => {
    const { container } = show(<StateStrips rowHeader="Session" strips={strips()} title="States" />)
    const form = (state: string) => {
      const rect = container.querySelector(`[data-state="${state}"] rect`)!
      return { height: Number(rect.getAttribute('height')), width: Number(rect.getAttribute('width')), fill: rect.getAttribute('fill') }
    }
    expect(form('active').height).toBe(18)
    expect(form('waiting').height).toBe(12)
    expect(form('idle').height).toBe(6)
    expect(form('unknown')).toMatchObject({ height: 18, fill: expect.stringMatching(/^url\(#/) })
    expect(form('ended').width).toBe(2)
  })
})

describe('heatmap', () => {
  test('moves by row with up and down, and lists the same values in its table', () => {
    const data = heatmap({ unreported: true })
    const { container } = show(<Heatmap data={data} rowHeader="Weekday" title="Calls" />)
    const group = plot(container)
    act(() => group.focus())
    expect(active(group)?.getAttribute('aria-label')).toMatch(/^Mon, 00:/)
    press(group, 'ArrowDown')
    expect(active(group)?.getAttribute('aria-label')).toMatch(/^Tue, 00:/)
    press(group, 'ArrowRight')
    expect(active(group)?.getAttribute('aria-label')).toMatch(/^Tue, 01:/)
    const cells = [...container.querySelectorAll('table tbody tr')].map((row) =>
      [...row.querySelectorAll('td')].map((cell) => (cell.querySelector('.sr-only') ? null : cell.textContent)),
    )
    expect(cells.map((row) => row.map((cell) => (cell === null ? null : Number(cell.replace(/,/g, '')))))).toEqual(data.values)
    expect(within(container).getByRole('list', { name: 'Scale' })).toBeInTheDocument()
    expect(group.querySelectorAll('rect[fill^="url(#"]').length).toBe(4)
  })
})

describe('tree', () => {
  test('draws each edge by its evidence tier, with its label, and moves to parent and child', () => {
    const { container } = show(<Tree root={agentTree()} rowHeader="Agent" title="Agents" />)
    const edges = [...container.querySelectorAll('[data-tier]')]
    expect(edges).toHaveLength(5)
    for (const edge of edges) {
      const tier = edge.getAttribute('data-tier') as keyof typeof TIER_DASH
      expect(edge.querySelector('path')?.getAttribute('stroke-dasharray') ?? undefined).toBe(TIER_DASH[tier])
      expect(edge.querySelector('text')?.textContent).toBeTruthy()
    }
    const legend = within(container).getByRole('list', { name: 'Legend' })
    expect(within(legend).getAllByRole('listitem')).toHaveLength(4)

    const group = plot(container)
    act(() => group.focus())
    expect(active(group)?.getAttribute('aria-label')).toMatch(/^Root: Session A/)
    press(group, 'ArrowRight')
    expect(active(group)?.getAttribute('aria-label')).toBe('from Session A (Claude Code), Task call (Tool call): Explorer')
    press(group, 'ArrowRight')
    expect(active(group)?.getAttribute('aria-label')).toMatch(/Grep worker$/)
    press(group, 'ArrowLeft')
    expect(active(group)?.getAttribute('aria-label')).toMatch(/Explorer$/)
    const rows = [...container.querySelectorAll('table tbody tr')].map((row) => row.textContent)
    expect(rows).toContain('Pi sessionCodex sessionSame folderHeuristic')
  })
})

describe('selection window', () => {
  function Controlled({ initial = [3, 6] as Range }) {
    const [range, setRange] = useState<Range>(initial)
    return <SelectionWindow data={daily()} rowHeader="Day" title="Range" value={range} onChange={setRange} />
  }
  const inside = (container: HTMLElement) =>
    [...container.querySelectorAll('table tbody tr')].flatMap((row, i) => (row.textContent?.endsWith('Yes') ? [i] : []))

  test('moves an edge with the arrow keys, and the whole window with Shift', () => {
    const { container } = show(<Controlled />)
    const [start, end] = screen.getAllByRole('slider')
    expect(start).toHaveAccessibleName('Start of the selected range')
    expect(end).toHaveAttribute('aria-valuenow', '6')
    fireEvent.keyDown(end, { key: 'ArrowRight' })
    expect(inside(container)).toEqual([3, 4, 5, 6, 7])
    fireEvent.keyDown(start, { key: 'ArrowRight', shiftKey: true })
    expect(inside(container)).toEqual([4, 5, 6, 7, 8])
    fireEvent.keyDown(start, { key: 'Home' })
    expect(inside(container)[0]).toBe(0)
    fireEvent.keyDown(end, { key: 'End' })
    expect(inside(container).at(-1)).toBe(13)
    // An edge cannot pass the other.
    fireEvent.keyDown(start, { key: 'End' })
    expect(start).toHaveAttribute('aria-valuenow', '13')
    expect(screen.getByText(/^Selected:/)).toHaveTextContent('Selected: Oct 18 to Oct 18')
    expect(unnamedControls(container)).toEqual([])
  })

  test('draws a new range by dragging outside it, picks a day by clicking, and extends with shift-click', () => {
    const { container } = show(<Controlled />)
    const track = container.querySelector<HTMLElement>('.touch-none')!
    const [start, end] = screen.getAllByRole('slider')
    const x0 = Number.parseFloat(start.style.left)
    const slot = (Number.parseFloat(end.style.left) - x0) / 4
    const box = track.getBoundingClientRect()
    const at = (i: number) => box.left + x0 + slot * (i - 3 + 0.5)
    // A drag from day 11 back to day 9 draws 9 to 11.
    fireEvent.pointerDown(track, { clientX: at(11), pointerId: 1 })
    fireEvent.pointerMove(track, { clientX: at(9), pointerId: 1 })
    fireEvent.pointerUp(track, { clientX: at(9), pointerId: 1 })
    expect(inside(container)).toEqual([9, 10, 11])
    // A click outside picks one day.
    fireEvent.pointerDown(track, { clientX: at(1), pointerId: 1 })
    fireEvent.pointerUp(track, { clientX: at(1), pointerId: 1 })
    expect(inside(container)).toEqual([1])
    // Shift-click extends it to the day clicked.
    fireEvent.pointerDown(track, { clientX: at(4), pointerId: 1, shiftKey: true })
    fireEvent.pointerUp(track, { clientX: at(4), pointerId: 1, shiftKey: true })
    expect(inside(container)).toEqual([1, 2, 3, 4])
    // A click inside the window, without a drag, picks that one day.
    fireEvent.pointerDown(track, { clientX: at(2), pointerId: 1 })
    fireEvent.pointerUp(track, { clientX: at(2), pointerId: 1 })
    expect(inside(container)).toEqual([2])
  })

  test('drags the window with the pointer', () => {
    const { container } = show(<Controlled />)
    const track = container.querySelector<HTMLElement>('.touch-none')!
    const [start] = screen.getAllByRole('slider')
    const x0 = Number.parseFloat(start.style.left)
    const slot = (Number.parseFloat(screen.getAllByRole('slider')[1].style.left) - x0) / 4
    const box = track.getBoundingClientRect()
    fireEvent.pointerDown(track, { clientX: box.left + x0 + slot * 1.5, pointerId: 1 })
    fireEvent.pointerMove(track, { clientX: box.left + x0 + slot * 4.5, pointerId: 1 })
    fireEvent.pointerUp(track, { pointerId: 1 })
    expect(inside(container)).toEqual([6, 7, 8, 9])
  })
})
