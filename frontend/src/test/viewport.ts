// The page width a test needs. jsdom has no layout, so there this does nothing; in a
// real browser (the WebKit run) it resizes the page, since below 1180px the layout
// moves navigation into the drawer and above it shows the bar and sidebar.
export const PHONE = 375
export const DESKTOP = 1280

export async function setWidth(width: number, height = 900): Promise<void> {
  if (navigator.userAgent.includes('jsdom')) return
  const { page } = await import('vitest/browser')
  await page.viewport(width, height)
}
