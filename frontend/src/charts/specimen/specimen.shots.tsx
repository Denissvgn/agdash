import { act, render } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { describe, expect, test } from 'vitest'
import { page, server } from 'vitest/browser'
import { specimenI18n } from './i18n'
import { SpecimenPage } from './SpecimenPage'

const WIDTHS = [1440, 390]
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
  test.each(WIDTHS)('at %ipx: no sideways scroll, every figure captured', async (width) => {
    await page.viewport(width, HEIGHT)
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
        path: `../../../specimen-shots/${server.browser}-${width}/${String(i).padStart(2, '0')}-${shot.name}.png`,
      })
    }
  })
})
