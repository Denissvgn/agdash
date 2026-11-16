import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactElement } from 'react'
import { I18nextProvider } from 'react-i18next'
import { describe, expect, test, vi } from 'vitest'
import { catalogue, createI18n, LANGUAGE } from '../i18n'
import { unnamedControls } from '../test/controls'
import { BarChart } from './BarChart'
import { ColumnChart } from './ColumnChart'
import type { TimeData } from './data'
import { LineChart } from './LineChart'
import { ShareBar } from './ShareBar'
import { SmallMultiples } from './SmallMultiples'
import { withSpecimen } from './specimen/i18n'
import { categories, daily, harnesses } from './specimen/samples'
import { SpecimenPage } from './specimen/SpecimenPage'

const OPEN = '⟦'
const CLOSE = '⟧'
type Tree = { [key: string]: string | Tree }
const wrap = (tree: Tree): Tree =>
  Object.fromEntries(
    Object.entries(tree).map(([key, value]) => [key, typeof value === 'string' ? `${OPEN}${value}${CLOSE}` : wrap(value)]),
  )

function show(ui: ReactElement, i18n = createI18n()) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>)
}

const SMALL: TimeData = daily(harnesses.slice(0, 2), { buckets: 5, gaps: [[2, 2]] })

const FIGURES: [string, () => ReactElement][] = [
  [
    'ranked bars',
    () => <BarChart categories={categories(false, true)} rowHeader="Tool" title="Calls" valueHeader="Calls" />,
  ],
  ['columns', () => <ColumnChart data={SMALL} rowHeader="Day" title="Sessions" />],
  ['lines', () => <LineChart data={SMALL} rowHeader="Day" title="Tokens" />],
  ['step areas', () => <LineChart data={SMALL} rowHeader="Day" title="Live" variant="step" />],
  ['small multiples', () => <SmallMultiples data={SMALL} rowHeader="Day" title="Per harness" />],
]

/** The tooltip's text, as a person sees it. */
const tooltip = (figure: HTMLElement) => figure.querySelector('[aria-hidden="true"].shadow-popover')?.textContent ?? null
const plot = (figure: HTMLElement) => figure.querySelector<HTMLElement>('[role="group"]')!
const active = (group: HTMLElement) => {
  const id = group.getAttribute('aria-activedescendant')
  return id ? document.getElementById(id) : null
}

describe.each(FIGURES)('%s', (_, figure) => {
  test('is one tab stop whose arrow keys move between marks and show the hover tooltip', () => {
    const { container } = show(figure())
    const outside = [...container.querySelectorAll('[tabindex="0"]')].filter((element) => !element.closest('details'))
    expect(outside).toHaveLength(1)
    const group = plot(container)
    expect(group).toHaveAttribute('tabindex', '0')

    act(() => group.focus())
    const first = active(group)
    expect(first).not.toBeNull()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    const second = active(group)
    expect(second).not.toBe(first)
    const keyed = tooltip(container)
    expect(keyed).toBeTruthy()
    // Each part of the tooltip is in the mark's accessible name.
    for (const part of container.querySelectorAll('.shadow-popover span span')) {
      expect(second?.getAttribute('aria-label')).toContain(part.textContent)
    }
    fireEvent.keyDown(group, { key: 'Escape' })
    expect(active(group)).toBeNull()
    expect(tooltip(container)).toBeNull()

    // The pointer over the same mark shows the same tooltip.
    act(() => group.blur())
    const origin = group.getBoundingClientRect()
    fireEvent.pointerMove(group, {
      clientX: origin.left + Number(second?.getAttribute('data-x')),
      clientY: origin.top + Number(second?.getAttribute('data-y')),
    })
    expect(tooltip(container)).toBe(keyed)
  })

  test('names every mark from the catalogue and lists the same values in its table twin', () => {
    const { container } = show(figure(), createI18n({ [LANGUAGE]: { translation: wrap(catalogue as Tree) } }))
    const marks = [...container.querySelectorAll('[role="img"]')]
    expect(marks.length).toBeGreaterThan(3)
    for (const mark of marks) {
      const name = mark.getAttribute('aria-label') ?? ''
      expect(name.startsWith(OPEN) && name.endsWith(CLOSE), name).toBe(true)
    }
    expect(unnamedControls(container)).toEqual([])

    const table = container.querySelector('table')!
    const cells = [...table.querySelectorAll('tbody td')].map((cell) =>
      cell.querySelector('.sr-only') ? 'not reported' : cell.textContent,
    )
    const named = marks.map((mark) => mark.getAttribute('aria-label') ?? '').join('\n')
    for (const cell of cells) {
      if (cell === 'not reported') expect(named).toContain('Not reported')
      else expect(named).toContain(cell)
    }
    expect(cells).toContain('not reported')
  })

  test('draws a value that was not reported hatched, never as zero', () => {
    const { container } = show(figure())
    expect(container.querySelector('pattern')).not.toBeNull()
    expect([...container.querySelectorAll('rect')].some((rect) => rect.getAttribute('fill')?.startsWith('url(#'))).toBe(true)
    const table = container.querySelector('table')!
    expect(within(table).getAllByText('not reported').length).toBeGreaterThan(0)
  })

  test('does not animate', () => {
    const { container } = show(figure())
    expect(container.querySelectorAll('animate, animateTransform, animateMotion, set')).toHaveLength(0)
    for (const element of container.querySelectorAll('figure [class]')) {
      expect(element.getAttribute('class')).not.toMatch(/\b(transition|animate-|duration-)/)
    }
  })
})

describe('the figure frame', () => {
  test('keeps the last drawing while new data loads, then says when there is none', () => {
    const ui = (props: { status: 'ready' | 'loading' | 'empty'; data?: TimeData }) => (
      <ColumnChart data={props.data} rowHeader="Day" status={props.status} title="Sessions" />
    )
    const i18n = createI18n()
    const view = render(<I18nextProvider i18n={i18n}>{ui({ status: 'ready', data: SMALL })}</I18nextProvider>)
    const drawn = view.container.querySelectorAll('path').length
    view.rerender(<I18nextProvider i18n={i18n}>{ui({ status: 'loading' })}</I18nextProvider>)
    expect(view.container.querySelectorAll('path')).toHaveLength(drawn)
    expect(screen.getByRole('status')).toHaveTextContent('Updating')
    view.rerender(<I18nextProvider i18n={i18n}>{ui({ status: 'ready', data: { ...SMALL, series: [] } })}</I18nextProvider>)
    expect(screen.getByText('Nothing to show')).toBeInTheDocument()
    expect(view.container.querySelector('[role="group"]')).toBeNull()
  })

  test('shows a placeholder before the first drawing, and a retry after a failure', () => {
    const retry = vi.fn()
    const view = show(<BarChart rowHeader="Tool" status="loading" title="Calls" valueHeader="Calls" />)
    expect(screen.getByRole('status', { name: 'Loading figure' })).toBeInTheDocument()
    view.unmount()
    show(<BarChart rowHeader="Tool" status="error" title="Calls" valueHeader="Calls" onRetry={retry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('This figure could not load')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalledOnce()
  })

  test('gives two or more series a legend, and one series none', () => {
    const one = show(<LineChart data={daily()} rowHeader="Day" title="One" />)
    expect(within(one.container).queryByRole('list', { name: 'Legend' })).toBeNull()
    one.unmount()
    const two = show(<LineChart data={daily(harnesses.slice(0, 2))} rowHeader="Day" title="Two" />)
    const legend = within(two.container).getByRole('list', { name: 'Legend' })
    expect(within(legend).getAllByRole('listitem').map((item) => item.textContent)).toEqual(harnesses.slice(0, 2))
  })

  test('folds a fourth series into Other rather than adding a fourth ink', () => {
    const { container } = show(<ColumnChart data={daily(harnesses)} rowHeader="Day" title="Four" />)
    const legend = within(container).getByRole('list', { name: 'Legend' })
    expect(within(legend).getAllByRole('listitem').map((item) => item.textContent)).toEqual([...harnesses.slice(0, 2), 'Other'])
  })
})

describe('the share bar', () => {
  test("draws a row's share of the largest, and hatches a value that was not reported", () => {
    const { container } = render(
      <>
        <ShareBar max={200} value={50} />
        <ShareBar max={200} value={null} />
      </>,
    )
    const [share, unreported] = container.querySelectorAll('[data-share]')
    expect(share).toHaveAttribute('data-share', '0.2500')
    expect(share).toHaveAttribute('aria-hidden', 'true')
    expect(unreported).toHaveAttribute('data-share', 'unreported')
    expect(unreported.querySelector('rect')?.getAttribute('fill')).toMatch(/^url\(#/)
  })
})

describe('the specimen page', () => {
  test('shows every component in every state, with copy from the catalogue', () => {
    const { container } = show(<SpecimenPage />, createI18n({ [LANGUAGE]: { translation: wrap(withSpecimen()) } }))
    // Five sections of seven states each, and the share-bar table.
    expect(container.querySelectorAll('figure')).toHaveLength(35)
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
    const stray: string[] = []
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent?.trim() ?? ''
      if (!text || (text.startsWith(OPEN) && text.endsWith(CLOSE))) continue
      if (node.parentElement?.closest('[data-figure-data]')) continue
      stray.push(text)
    }
    expect(stray).toEqual([])
  })
})
