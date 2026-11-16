import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import App from '../App'

// jsdom has no fonts, so this runs in the real browsers only (the WebKit run).
const browser = !navigator.userAgent.includes('jsdom')

test.skipIf(!browser)('the app font loads and is the one the page uses', async () => {
  window.location.hash = '#/no-such-page'
  render(<App />)
  expect(await screen.findByRole('heading', { level: 1 })).toBeInTheDocument()
  const loaded = await document.fonts.load('16px "Inter Variable"')
  expect(loaded.length).toBeGreaterThan(0)
  expect(loaded.every((face) => face.status === 'loaded')).toBe(true)
  expect(document.fonts.check('16px "Inter Variable"')).toBe(true)
  expect(getComputedStyle(document.body).fontFamily).toMatch(/^"?Inter Variable"?/)
})
