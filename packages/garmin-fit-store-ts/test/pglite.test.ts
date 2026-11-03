// The pglite gate: the fit migrations on PGlite with the TimescaleDB fork, a corpus file imported through the TypeScript
// store, exported, and the R4 oracle checked. Needs PGLITE_DIR (a build of endosome/pglite, branch timescaledb).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { decode, toRows } from '../src/codec.ts'
import { exportFile, importFile } from '../src/store.ts'

const root = process.env.PGLITE_DIR
const corpus = new URL('../../garmin-fit-store/tests/corpus/', import.meta.url)
const migrations = new URL('../../garmin-fit-store/src/garmin_fit_store/migrations/', import.meta.url)

test('corpus files round-trip through PGlite and the TypeScript store', { skip: !root && 'set PGLITE_DIR' }, async () => {
  const load = (p: string) => import(pathToFileURL(`${root}/${p}`).href)
  const { PGlite } = await load('packages/pglite/dist/index.js')
  const { timescaledb } = await load('packages/pglite-extensions/pglite-timescaledb/dist/index.js')
  const pg = await PGlite.create({ extensions: { timescaledb } })
  for (const name of readdirSync(migrations).filter((n) => n.endsWith('.sql')).sort()) await pg.exec(readFileSync(new URL(name, migrations), 'utf8'))
  const rows = (f: ReturnType<typeof decode>) => JSON.stringify(toRows(f), (_k, v) => (typeof v === 'bigint' ? String(v) : v))
  for (const name of readdirSync(corpus).filter((n) => n.endsWith('.fit'))) {
    const data = readFileSync(new URL(name, corpus))
    const id = await importFile(pg, data, false)
    assert.equal(await importFile(pg, data, false), id, `${name}: a second import returns the first row`)
    const exported = await exportFile(pg, id)
    assert.equal(rows(decode(exported)), rows(decode(data, false)), name)
  }
  await pg.close()
})
