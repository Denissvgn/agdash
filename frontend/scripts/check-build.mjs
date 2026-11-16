// The production build carries only the app: the chart-kit specimen is a
// development page, so neither its page nor its code may reach dist/.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const DIST = new URL('../dist/', import.meta.url).pathname
const files = (directory) =>
  readdirSync(directory).flatMap((name) => {
    const path = join(directory, name)
    return statSync(path).isDirectory() ? files(path) : [path]
  })

const copy = JSON.parse(readFileSync(new URL('../src/charts/specimen/copy.json', import.meta.url), 'utf8'))
const markers = ['SpecimenPage', copy.charts.specimen.intro]
const found = files(DIST).filter(
  (path) =>
    /specimen/i.test(path) ||
    (/\.(js|html)$/.test(path) && markers.some((marker) => readFileSync(path, 'utf8').includes(marker))),
)
if (found.length) {
  console.error(`The specimen reached the production build:\n${found.join('\n')}`)
  process.exit(1)
}
console.log(`dist/ holds ${files(DIST).length} files and no specimen.`)
