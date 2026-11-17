// Draw the app's icon files from their two SVG sources in desktop/icons:
// icon.svg, and icon-small.svg for 32 pixels and under, where the first is too fine.
// The small one is also copied to public/favicon.svg, the page's icon and the mark
// beside the name. The .icns is built with iconutil, so that part runs on macOS only.
//   node scripts/icons.mjs
import { execFileSync } from 'node:child_process'
import { copyFileSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { webkit } from 'playwright'

const ICONS = fileURLToPath(new URL('../../desktop/icons/', import.meta.url))
const SMALL_UP_TO = 32
const source = (size) => readFileSync(join(ICONS, size <= SMALL_UP_TO ? 'icon-small.svg' : 'icon.svg'), 'utf8')

const browser = await webkit.launch()
const page = await browser.newPage({ deviceScaleFactor: 1 })

async function draw(size, path) {
  await page.setViewportSize({ width: size, height: size })
  await page.setContent(
    `<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${source(size)}`,
  )
  await page.screenshot({ path, omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } })
}

copyFileSync(join(ICONS, 'icon-small.svg'), fileURLToPath(new URL('../public/favicon.svg', import.meta.url)))
await draw(32, join(ICONS, '32x32.png'))
await draw(128, join(ICONS, '128x128.png'))
await draw(512, join(ICONS, 'icon.png'))

if (process.platform === 'darwin') {
  const set = join(mkdtempSync(join(tmpdir(), 'agdash-icon-')), 'icon.iconset')
  execFileSync('mkdir', [set])
  for (const points of [16, 32, 128, 256, 512]) {
    await draw(points, join(set, `icon_${points}x${points}.png`))
    await draw(points * 2, join(set, `icon_${points}x${points}@2x.png`))
  }
  execFileSync('iconutil', ['--convert', 'icns', '--output', join(ICONS, 'icon.icns'), set])
  rmSync(join(set, '..'), { recursive: true })
}
await browser.close()
