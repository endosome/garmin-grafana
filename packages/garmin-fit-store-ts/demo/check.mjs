// Opens demo/index.html in headless Chromium: serves the demo with the PGlite build and the migrations, imports a corpus file
// through the page and checks that the exported download decodes to the same messages.
// PGLITE_DIR=~/pglite node demo/check.mjs   (needs playwright-core, e.g. from ../../e2e/node_modules)
import { createServer } from 'node:http'
import { readFileSync, existsSync, statSync } from 'node:fs'
import { createRequire } from 'node:module'
import { extname, join, normalize } from 'node:path'
import { decode, toRows } from '../src/codec.ts'

const here = new URL('.', import.meta.url).pathname
const pglite = join(process.env.PGLITE_DIR, 'packages/pglite/dist')
const timescale = join(process.env.PGLITE_DIR, 'packages/pglite-extensions/pglite-timescaledb/dist')
const migrations = join(here, '../../garmin-fit-store/src/garmin_fit_store/migrations')
const roots = [['/pglite/', pglite], ['/timescaledb/', timescale], ['/migrations/', migrations], ['/', here]]
const types = { '.js': 'text/javascript', '.html': 'text/html', '.sql': 'text/plain', '.wasm': 'application/wasm' }
const server = createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname)
  for (const [prefix, dir] of roots) {
    if (!path.startsWith(prefix)) continue
    const file = normalize(join(dir, path.slice(prefix.length) || 'index.html'))
    if (file.startsWith(dir) && existsSync(file) && statSync(file).isFile()) {
      res.writeHead(200, { 'content-type': types[extname(file)] ?? 'application/octet-stream' })
      return res.end(readFileSync(file))
    }
  }
  res.writeHead(404).end()
}).listen(0)
const require = createRequire(new URL('../../../e2e/', import.meta.url))
const { chromium } = require('playwright-core')
const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] })
const page = await browser.newPage({ acceptDownloads: true })
page.on('console', (m) => m.type() === 'error' && console.log('console error:', m.text()))
page.on('pageerror', (e) => console.log('page error:', e.message))
await page.goto(`http://localhost:${server.address().port}/`)
await page.waitForFunction(() => document.getElementById('log').textContent.includes('Ready.'), null, { timeout: 120000 })
const source = join(here, '../../garmin-fit-store/tests/corpus/activity.fit')
await page.setInputFiles('#file', source)
await page.waitForSelector('#download:not([hidden])', { timeout: 60000 }).catch(async (e) => { console.log(await page.textContent('#log')); throw e })
const [download] = await Promise.all([page.waitForEvent('download'), page.click('#download')])
const exported = readFileSync(await download.path())
console.log(await page.textContent('#log'))
const rows = (f) => JSON.stringify(toRows(f), (_k, v) => (typeof v === 'bigint' ? String(v) : v))
const ok = rows(decode(exported)) === rows(decode(readFileSync(source)))
console.log(ok ? 'demo: exported file matches the original' : 'demo: DIFFERENT')
await browser.close(); server.close()
process.exit(ok ? 0 : 1)
