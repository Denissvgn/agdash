import { act, render } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { afterEach, describe, expect, test } from 'vitest'
import { page, server } from 'vitest/browser'
import dark from '../../theme/workchord-dark.json'
import tokens from '../../theme/workchord-tokens.json'
import { specimenI18n } from './i18n'
import { SpecimenPage } from './SpecimenPage'

const WIDTHS = [1440, 390]
// Each theme is pinned on the root, as data-theme allows, and must draw its own canvas.
const THEMES = { light: tokens.colors['canvas-mist'], dark: dark.colors['canvas-mist'].value }
const CASES = (Object.keys(THEMES) as (keyof typeof THEMES)[]).flatMap((theme) => WIDTHS.map((width) => ({ theme, width })))
const channels = (colour: string) => (colour.match(/\d+/g) ?? []).slice(0, 3).join(' ')
const HEIGHT = 1000
// Below the browser window's 2000px, so no frame is ever scaled down to fit.
const TALLEST = 1900
const settle = () => new Promise((resolve) => window.setTimeout(resolve, 50))
const slug = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 40)

describe('the chart-kit specimen', () => {
  afterEach(() => {
    delete document.documentElement.dataset.theme
  })

  test.each(CASES)('$theme at $width px: no sideways scroll, every figure captured', async ({ theme, width }) => {
    await page.viewport(width, HEIGHT)
    document.documentElement.dataset.theme = theme
    document.body.className = 'bg-canvas-mist'
    render(
      <I18nextProvider i18n={specimenI18n()}>
        <SpecimenPage />
      </I18nextProvider>,
    )
    await act(async () => {
      await document.fonts.ready
      await settle()
    })
    expect(window.innerWidth, 'the test frame has the width asked for').toBe(width)
    expect(channels(getComputedStyle(document.body).backgroundColor), `the ${theme} canvas`).toBe(channels(THEMES[theme]))
    const figure = document.querySelector('figure')!
    const surface = theme === 'dark' ? dark.colors['working-paper'].value : tokens.colors['working-paper']
    expect(channels(getComputedStyle(figure).backgroundColor), `the ${theme} figure surface`).toBe(channels(surface))

    const root = document.documentElement
    expect(root.scrollWidth, 'the page scrolls sideways').toBeLessThanOrEqual(root.clientWidth)
    for (const figure of document.querySelectorAll('figure')) {
      expect(figure.getBoundingClientRect().right).toBeLessThanOrEqual(root.clientWidth + 0.5)
    }

    // The page heading, then each figure under its section's name, then the share-bar table.
    const shots: { element: HTMLElement; name: string }[] = [
      { element: document.querySelector<HTMLElement>('main > header')!, name: 'page' },
    ]
    for (const section of document.querySelectorAll<HTMLElement>('main > section')) {
      const heading = slug(section.querySelector('h2')?.textContent ?? 'section')
      const figures = [...section.querySelectorAll<HTMLElement>('figure')]
      if (!figures.length) shots.push({ element: section, name: heading })
      figures.forEach((figure, i) =>
        shots.push({ element: figure, name: `${heading}-${i + 1}-${slug(figure.querySelector('h3')?.textContent ?? '')}` }),
      )
    }
    expect(shots).toHaveLength(69)

    for (const [i, shot] of shots.entries()) {
      const tall = Math.ceil(shot.element.getBoundingClientRect().height) + 48
      expect(tall, `${shot.name} fits one capture`).toBeLessThanOrEqual(TALLEST)
      await page.viewport(width, Math.max(HEIGHT, tall))
      await act(settle)
      await page.screenshot({
        element: shot.element,
        path: `../../../specimen-shots/${server.browser}-${width}${theme === 'dark' ? '-dark' : ''}/${String(i).padStart(2, '0')}-${shot.name}.png`,
      })
    }
  })
})
