import { act, render } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { describe, expect, test } from 'vitest'
import { page, server } from 'vitest/browser'
import { specimenI18n } from './i18n'
import { SpecimenPage } from './SpecimenPage'

const WIDTHS = [1440, 390]
const settle = () => new Promise((resolve) => window.setTimeout(resolve, 50))

describe('the chart-kit specimen', () => {
  test.each(WIDTHS)('at %ipx: no sideways scroll, every section captured', async (width) => {
    await page.viewport(width, 900)
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

    const page_ = document.documentElement
    expect(page_.scrollWidth, 'the page scrolls sideways').toBeLessThanOrEqual(page_.clientWidth)
    for (const figure of document.querySelectorAll('figure')) {
      expect(figure.getBoundingClientRect().right).toBeLessThanOrEqual(page_.clientWidth + 0.5)
    }

    const sections = [...document.querySelectorAll<HTMLElement>('main > header, main > section')]
    expect(sections.length).toBe(7)
    for (const [i, section] of sections.entries()) {
      // Tall enough to hold the section, so it is captured whole.
      await page.viewport(width, Math.max(900, Math.ceil(section.getBoundingClientRect().height) + 64))
      await act(settle)
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(document.documentElement.clientWidth)
      await page.screenshot({
        element: section,
        path: `../../../specimen-shots/${server.browser}-${width}-${String(i).padStart(2, '0')}.png`,
      })
    }
  })
})
