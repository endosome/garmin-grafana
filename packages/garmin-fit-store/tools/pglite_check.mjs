// Applies the fit migrations under the PGlite + TimescaleDB fork and stores and reads back every conformance corpus file
// (as its expected JSONL). Usage: PGLITE_DIR=~/pglite node tools/pglite_check.mjs
import { readFileSync, readdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'

const root = process.env.PGLITE_DIR
if (!root) throw new Error('set PGLITE_DIR to a checkout of endosome/pglite (branch timescaledb)')
const load = (path) => import(pathToFileURL(`${root}/${path}`).href)
const { PGlite } = await load('packages/pglite/dist/index.js')
const { timescaledb } = await load('packages/pglite-extensions/pglite-timescaledb/dist/index.js')

const here = new URL('.', import.meta.url)
const migrations = readdirSync(new URL('../src/garmin_fit_store/migrations/', here)).filter((n) => n.endsWith('.sql')).sort()
const pg = await PGlite.create({ extensions: { timescaledb } })
console.log((await pg.query('select version()')).rows[0].version)
for (const name of migrations) await pg.exec(readFileSync(new URL(`../src/garmin_fit_store/migrations/${name}`, here), 'utf8'))

const EPOCH = 631065600
let failures = 0
for (const name of readdirSync(new URL('../tests/corpus/', here)).filter((n) => n.endsWith('.jsonl')).sort()) {
  const lines = readFileSync(new URL(`../tests/corpus/${name}`, here), 'utf8').trim().split('\n').map((l) => JSON.parse(l))
  const header = lines[0].header
  const { rows: [{ id }] } = await pg.query(
    `INSERT INTO fit.files(sha256, protocol_version, profile_number, sdk_version, message_count)
     VALUES (decode(md5($1), 'hex'), $2, $3, 'pglite-check', $4) RETURNING id`, [name, header.protocol, header.profile, lines.length])
  const messages = lines.filter((l) => l.msg).map((l) => l.msg)
  for (const d of lines.filter((l) => l.def).map((l) => l.def))
    await pg.query('INSERT INTO fit.definitions VALUES ($1,$2,$3,$4,$5,$6)', [id, d.id, d.global, d.arch, JSON.stringify(d.fields), JSON.stringify(d.dev)])
  for (const m of messages) {
    const stamp = m.f['253']
    const time = typeof stamp === 'number' && stamp >= 0x10000000 ? new Date((stamp + EPOCH) * 1000).toISOString() : null
    if (m.global === 20 && time) await pg.query('INSERT INTO fit.records VALUES ($1,$2,$3,$4,$5,$6)', [id, m.seq, m.def, time, JSON.stringify(m.f), JSON.stringify(m.d)])
    else await pg.query('INSERT INTO fit.messages VALUES ($1,$2,$3,$4,$5,$6,$7)', [id, m.seq, m.def, m.global, time, JSON.stringify(m.f), JSON.stringify(m.d)])
  }
  const { rows } = await pg.query(
    `SELECT seq, fields, dev FROM (SELECT seq, fields, dev FROM fit.messages WHERE file_id = $1
       UNION ALL SELECT seq, fields, dev FROM fit.records WHERE file_id = $1) m ORDER BY seq`, [id])
  const same = rows.length === messages.length && rows.every((r, i) => JSON.stringify(r.fields) === JSON.stringify(messages[i].f) || sorted(r.fields) === sorted(messages[i].f))
  console.log(`${name}: ${rows.length} messages ${same ? 'ok' : 'DIFFERENT'}`)
  failures += same ? 0 : 1
}
function sorted(o) { return JSON.stringify(Object.entries(o).sort()) }
const { rows } = await pg.query("SELECT count(*)::int AS chunks FROM timescaledb_information.chunks WHERE hypertable_name = 'records'")
console.log('record chunks:', rows[0].chunks)
await pg.close()
process.exit(failures ? 1 : 0)
