// Acceptance of every figure screen in the built app, served over the synthetic store
// in backend/tests/screens.py:
//
//   (cd backend && ../.venv/bin/python tests/screens.py --port 8790 --ui ../frontend/dist) &
//   node scripts/screens.mjs --base http://127.0.0.1:8790 --engine webkit
//   node scripts/screens.mjs --base http://127.0.0.1:8790 --engine chromium --forced-colors
//
// At a desktop width, at 200% zoom (half the width at twice the density) and at 320px,
// every screen is checked for sideways scroll, figure text cut off at a figure's edge
// and anything in a figure that animates. At the desktop width each figure is reached
// with Tab alone, answers the arrow, Home and End keys with a labelled mark, and its
// accessibility tree is written out for the screen-reader review. Every screen is
// captured; with --forced-colors (Chromium only) the captures are in forced colours, each
// figure is checked to keep its marks visible, and a chosen pill to look unlike the rest.
// Exits non-zero on any failure, after listing them all.
import { mkdirSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { parseArgs } from 'node:util'

const playwright = createRequire(import.meta.url)('playwright')
const { values } = parseArgs({
  options: {
    base: { type: 'string', default: 'http://127.0.0.1:8790' },
    token: { type: 'string', default: 'screens-token' },
    engine: { type: 'string', default: 'webkit' },
    'forced-colors': { type: 'boolean', default: false },
    out: { type: 'string', default: new URL('../specimen-shots/screens/', import.meta.url).pathname },
  },
})
const forced = values['forced-colors']
if (forced && values.engine !== 'chromium') throw new Error('Forced colours can only be emulated in Chromium')

const LONG_TITLE = 'Migrate the billing reports'
// The desktop run allows motion, so a figure that animates at all is caught; the others
// ask for reduced motion, as a reader who zooms or reflows may.
const MODES = [
  { name: 'desktop', width: 1440, height: 900, scale: 1, keyboard: true, motion: 'no-preference' },
  // 200% browser zoom of a 1440px window: half the CSS width at twice the density.
  { name: 'zoom-200', width: 720, height: 450, scale: 2, motion: 'reduce' },
  { name: 'reflow-320', width: 320, height: 640, scale: 2, motion: 'reduce' },
]

async function longSession() {
  const answer = await fetch(`${values.base}/api/sessions?limit=200`, { headers: { Authorization: `Bearer ${values.token}` } })
  if (!answer.ok) throw new Error(`The synthetic store did not answer: ${answer.status}`)
  const found = (await answer.json()).items.find((item) => item.title === LONG_TITLE)
  if (!found) throw new Error('The synthetic store has no long session')
  return found.id
}

const SCREENS = [
  // The live board's lanes sit in list rows and are not figures: each row says the same in words.
  // At 320px the rows drop their lanes, so the board itself is what to wait for.
  { name: 'live', hash: '#/live', ready: '[data-roster]', strips: true },
  { name: 'live-agents', hash: '#/live?view=agents', ready: '[data-agents] a[data-row]', strips: true },
  { name: 'history', hash: '#/history' },
  { name: 'session', hash: `#/history/sessions/${await longSession()}` },
  { name: 'projects', hash: '#/projects' },
  { name: 'projects-30', hash: '#/projects?days=30' },
  { name: 'usage', hash: '#/analytics' },
  { name: 'tools', hash: '#/analytics/tools' },
  { name: 'activity', hash: '#/analytics/activity' },
]

const failures = new Map()
const fail = (where, message) => {
  const line = `${where}: ${message}`
  failures.set(line, (failures.get(line) ?? 0) + 1)
}

/** Wait until every figure on the page has drawn: none loading, and the count steady. */
async function settle(page, screen) {
  if (screen.ready) await page.waitForSelector(screen.ready, { timeout: 20_000 })
  await page.waitForLoadState(screen.ready ? 'load' : 'networkidle')
  let last = -1
  for (let tries = 0; tries < 40; tries++) {
    const state = await page.evaluate(() => ({
      figures: document.querySelectorAll('figure').length,
      busy: document.querySelectorAll('figure [aria-busy="true"]').length,
    }))
    if (state.figures > 0 && state.busy === 0 && state.figures === last) break
    last = state.figures
    await page.waitForTimeout(250)
  }
  await page.evaluate(() => document.fonts.ready)
}

/** Sideways scroll, figure text past its figure's edge, text cut off, and any motion. */
async function layout(page, where, screen) {
  const found = await page.evaluate((strips) => {
    const problems = []
    const root = document.documentElement
    if (root.scrollWidth > root.clientWidth + 0.5) problems.push(`the page scrolls sideways (${root.scrollWidth} > ${root.clientWidth})`)
    const figures = [...document.querySelectorAll('figure')]
    if (strips) {
      // Hidden from assistive technology, never focusable, inside the page, and said in words in the row.
      const drawn = [...document.querySelectorAll('svg[data-strip]')]
      if (!drawn.length) problems.push('no live strip on the screen')
      for (const svg of drawn) {
        if (!svg.closest('[aria-hidden="true"]')) problems.push(`a ${svg.dataset.strip} strip is not hidden from assistive technology`)
        if (svg.getBoundingClientRect().right > root.clientWidth + 0.5) problems.push(`a ${svg.dataset.strip} strip runs past the page's edge`)
        const row = svg.parentElement?.closest('[aria-hidden="true"]')?.parentElement
        if (!row?.querySelector('.sr-only')?.textContent?.trim()) problems.push(`a ${svg.dataset.strip} strip's row does not say it in words`)
      }
    } else if (!figures.length) problems.push('no figure on the screen')
    figures.forEach((figure, f) => {
      const name = figure.querySelector('h3')?.textContent?.trim() || `figure ${f + 1}`
      const box = figure.getBoundingClientRect()
      if (box.right > root.clientWidth + 0.5) problems.push(`${name}: runs past the page's edge`)
      for (const text of figure.querySelectorAll('svg text')) {
        const r = text.getBoundingClientRect()
        if (!r.width) continue
        if (r.left < box.left - 0.5 || r.right > box.right + 0.5) problems.push(`${name}: "${text.textContent}" runs past the figure's edge`)
      }
      for (const element of figure.querySelectorAll('*')) {
        const style = getComputedStyle(element)
        if (element.closest('details:not([open])') && element.tagName !== 'SUMMARY' && !element.matches('details')) continue
        // Text for screen readers only is clipped to a pixel on purpose.
        const spoken = element.closest('.sr-only') || element.getBoundingClientRect().width <= 1
        const clipsX = ['hidden', 'clip'].includes(style.overflowX) && element.scrollWidth > element.clientWidth + 1
        if (!spoken && clipsX && !style.textOverflow.includes('ellipsis') && element.textContent.trim()) {
          problems.push(`${name}: text cut off in <${element.tagName.toLowerCase()}> "${element.textContent.trim().slice(0, 40)}"`)
        }
        if (style.animationName !== 'none') problems.push(`${name}: <${element.tagName.toLowerCase()}> animates (${style.animationName})`)
        // Reduced motion shortens every transition to 0.01ms; anything longer moves.
        const moving = style.transitionDuration.split(',').some((d) => parseFloat(d) * (d.trim().endsWith('ms') ? 1 : 1000) > 0.01)
        if (moving) problems.push(`${name}: <${element.tagName.toLowerCase()}> has a transition (${style.transitionProperty})`)
      }
    })
    return problems
  }, !!screen.strips)
  for (const message of found) fail(where, message)
}

/** A selection window's handles: each reached by Tab, and moved by the arrow keys. */
async function sliders(page, where) {
  const handles = page.locator('figure [role="slider"]')
  const count = await handles.count()
  for (let i = 0; i < count; i++) {
    const handle = handles.nth(i)
    const name = await handle.getAttribute('aria-label')
    await page.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined))
    let reached = false
    for (let press = 0; press < 200 && !reached; press++) {
      await page.keyboard.press('Tab')
      reached = await handle.evaluate((element) => element === document.activeElement)
    }
    if (!reached) fail(where, `Tab never reached the "${name}" handle`)
    await handle.focus()
    const before = await handle.getAttribute('aria-valuenow')
    // A window that scrolls a list follows the scroll some frames later, and later still on a
    // busy machine, so the value is waited for rather than read after a fixed pause. A handle
    // already at one end cannot go further that way, so the other arrow is tried before failing.
    let moved = false
    for (const key of i % 2 ? ['ArrowLeft', 'ArrowRight'] : ['ArrowRight', 'ArrowLeft']) {
      await page.keyboard.press(key)
      for (let waited = 0; waited < 3000 && !moved; waited += 100) {
        await page.waitForTimeout(100)
        moved = (await handle.getAttribute('aria-valuenow')) !== before
      }
      if (moved) break
    }
    if (!moved) fail(where, `the arrow keys left the "${name}" handle where it was`)
    if (!(await handle.getAttribute('aria-valuetext'))) fail(where, `the "${name}" handle does not say its value in words`)
  }
  return count
}

/** Tab to each figure's plot, then walk its marks; returns each figure's accessibility tree. */
async function keyboard(page, where) {
  const plots = await page.locator('figure [role="group"][tabindex="0"]').count()
  const handles = await sliders(page, where)
  if (!plots && !handles) fail(where, 'no figure takes keyboard focus')
  if (!plots) {
    const trees = []
    for (const figure of await page.locator('figure').all()) {
      trees.push({ figure: ((await figure.locator('h3').first().textContent()) ?? '').trim(), tree: await figure.ariaSnapshot() })
    }
    return trees
  }
  // Only the plot itself takes focus inside the drawing.
  const stray = await page.evaluate(() =>
    [...document.querySelectorAll('figure svg')].flatMap((svg) =>
      [...svg.querySelectorAll('a, button, input, [tabindex]')].filter((el) => el.getAttribute('tabindex') !== '-1').map((el) => el.outerHTML.slice(0, 60)),
    ),
  )
  for (const element of stray) fail(where, `a focusable element inside a drawing: ${element}`)

  // From the top of the page, Tab alone must reach every plot.
  await page.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined))
  const reached = new Set()
  for (let press = 0; press < 400 && reached.size < plots; press++) {
    await page.keyboard.press('Tab')
    const at = await page.evaluate(() => {
      const active = document.activeElement
      if (!active?.matches('figure [role="group"][tabindex="0"]')) return null
      return [...document.querySelectorAll('figure [role="group"][tabindex="0"]')].indexOf(active)
    })
    if (at !== null && at >= 0) reached.add(at)
  }
  if (reached.size < plots) fail(where, `Tab reached ${reached.size} of ${plots} figures`)

  const trees = []
  for (let i = 0; i < plots; i++) {
    const plot = page.locator('figure [role="group"][tabindex="0"]').nth(i)
    const figure = plot.locator('xpath=ancestor::figure[1]')
    const name = ((await figure.locator('h3').first().textContent()) ?? `figure ${i + 1}`).trim()
    await plot.focus()
    const marks = []
    for (const key of ['ArrowRight', 'ArrowRight', 'End', 'Home']) {
      await page.keyboard.press(key)
      marks.push(
        await plot.evaluate((element) => {
          const id = element.getAttribute('aria-activedescendant')
          const mark = id ? document.getElementById(id) : null
          return mark ? mark.getAttribute('aria-label') : null
        }),
      )
    }
    if (marks.some((label) => !label)) fail(where, `${name}: a key left no labelled mark active (${JSON.stringify(marks)})`)
    else if (marks[2] === marks[3] && (await plot.evaluate((el) => el.querySelectorAll('[role="img"]').length)) > 1) {
      fail(where, `${name}: Home and End land on the same mark`)
    }
    const tooltip = await figure.locator('.shadow-popover').count()
    if (!tooltip) fail(where, `${name}: no tooltip for the active mark`)
    trees.push({ figure: name, keys: { ArrowRight: marks[0], End: marks[2], Home: marks[3] }, tree: await figure.ariaSnapshot() })
    await page.keyboard.press('Escape')
  }
  return trees
}

/** In forced colours, every mark and texture must still be drawn in a colour unlike its surface. */
async function forcedColours(page, where) {
  const found = await page.evaluate(() => {
    const problems = []
    for (const figure of document.querySelectorAll('figure')) {
      const name = figure.querySelector('h3')?.textContent?.trim() ?? 'a figure'
      const surface = getComputedStyle(figure).backgroundColor
      let drawn = 0
      for (const mark of figure.querySelectorAll('svg rect, svg circle, svg path, svg line')) {
        const style = getComputedStyle(mark)
        const paints = [style.fill, style.stroke].filter((paint) => paint && paint !== 'none' && paint !== surface && !paint.endsWith(', 0)'))
        if (paints.length) drawn++
      }
      if (figure.querySelector('svg') && !drawn) problems.push(`${name}: nothing in the drawing differs from its surface`)
    }
    // The switches that steer the figures: a chosen pill must look unlike the others.
    const pills = [...document.querySelectorAll('.toggle-chip')]
    const chosen = (pill) => pill.matches('[aria-pressed="true"]') || !!pill.querySelector(':checked')
    const look = (pill) => getComputedStyle(pill).backgroundColor
    const plain = new Set(pills.filter((pill) => !chosen(pill)).map(look))
    for (const pill of pills.filter(chosen)) {
      if (plain.has(look(pill))) problems.push(`the chosen "${pill.textContent.trim()}" pill looks like the others`)
    }
    return problems
  })
  for (const message of found) fail(where, message)
}

const browser = await playwright[values.engine].launch()
const engine = forced ? `${values.engine}-forced-colors` : values.engine
const out = join(values.out, engine)
const trees = {}
for (const mode of MODES) {
  const context = await browser.newContext({
    viewport: { width: mode.width, height: mode.height },
    deviceScaleFactor: mode.scale,
    reducedMotion: mode.motion,
    ...(forced ? { forcedColors: 'active' } : {}),
  })
  const page = await context.newPage()
  page.on('pageerror', (error) => {
    // WebKit words a request that leaving the page cancelled as failing "access control
    // checks". These pages only ask their own service, where no such check can fail, so
    // that is the check's own next step cutting a request short, not an error of the page.
    const cancelled = error.message.includes('due to access control checks') && error.message.includes(new URL(values.base).host)
    if (!cancelled) fail(`${engine} ${mode.name}`, `page error: ${error.message}`)
  })
  // The way `agdash open` hands a browser tab its token: the tab keeps it for later pages.
  await page.goto(`${values.base}/#agdash-token=${encodeURIComponent(values.token)}`)
  await page.waitForFunction(() => !window.location.hash.startsWith('#agdash-token='))
  mkdirSync(join(out, mode.name), { recursive: true })
  for (const screen of SCREENS) {
    const where = `${engine} ${mode.name} ${screen.name}`
    await page.goto(`${values.base}/${screen.hash}`)
    await settle(page, screen)
    await layout(page, where, screen)
    if (forced) await forcedColours(page, where)
    if (mode.keyboard && !forced && !screen.strips) trees[screen.name] = await keyboard(page, where)
    await page.screenshot({ path: join(out, mode.name, `${screen.name}.png`), fullPage: true })
    console.log(`${where}: checked`)
  }
  await context.close()
}
await browser.close()
if (Object.keys(trees).length) writeFileSync(join(out, 'accessibility.json'), `${JSON.stringify(trees, null, 2)}\n`)

if (failures.size) {
  const lines = [...failures].map(([line, times]) => `  - ${line}${times > 1 ? ` (×${times})` : ''}`)
  console.error(`\n${failures.size} problem(s):\n${lines.join('\n')}`)
  process.exit(1)
}
console.log(`\nEvery figure screen passed in ${engine}.`)
