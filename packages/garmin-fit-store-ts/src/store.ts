// The TypeScript half of the store: a FIT file into the fit.* tables and back, over any PGlite-like database.
import { decode, encode, parseJson, stringifyJson, type FitFile, type Value } from './codec.ts'

export interface Db {
  query(sql: string, params?: unknown[]): Promise<{ rows: any[] }>
}

const EPOCH = 631065600
const hex = (bytes: ArrayBuffer) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, '0')).join('')

export async function importFile(db: Db, data: Uint8Array, checkCrc = true): Promise<number> {
  const file = decode(data, checkCrc)
  const digest = hex(await crypto.subtle.digest('SHA-256', data as BufferSource))
  const known = await db.query(`SELECT id FROM fit.files WHERE sha256 = decode($1, 'hex') AND parent_id IS NULL AND source_id IS NULL`, [digest])
  if (known.rows.length) return known.rows[0].id
  const stamps = file.messages.map((m) => m.f.get(253)).filter((t): t is number => typeof t === 'number' && t >= 0x10000000)
  const iso = (t: number | undefined) => (t === undefined ? null : new Date((t + EPOCH) * 1000).toISOString())
  await db.query('BEGIN')
  try {
    const { rows } = await db.query(
      `INSERT INTO fit.files(sha256, protocol_version, profile_number, sdk_version, message_count, start_time, end_time)
       VALUES (decode($1, 'hex'), $2, $3, 'typescript', $4, $5, $6) RETURNING id`,
      [digest, file.protocol, file.profile, file.messages.length, iso(stamps.length ? Math.min(...stamps) : undefined), iso(stamps.length ? Math.max(...stamps) : undefined)])
    const id: number = rows[0].id
    await db.query(`INSERT INTO fit.blobs VALUES ($1, decode($2, 'hex'))`, [id, hex(data.buffer.slice(data.byteOffset, data.byteOffset + data.length) as ArrayBuffer)])
    for (const d of file.definitions)
      await db.query('INSERT INTO fit.definitions VALUES ($1,$2,$3,$4,$5,$6)', [id, d.id, d.global, d.arch, JSON.stringify(d.fields), JSON.stringify(d.dev)])
    for (const m of file.messages) {
      const stamp = m.f.get(253)
      const time = typeof stamp === 'number' && stamp >= 0x10000000 ? iso(stamp) : null
      const fields = stringifyJson(Object.fromEntries([...m.f].map(([k, v]) => [String(k), v])))
      const dev = stringifyJson(Object.fromEntries(m.d))
      if (m.global === 20 && time) await db.query('INSERT INTO fit.records(file_id, seq, def_id, time, fields, dev) VALUES ($1,$2,$3,$4,$5,$6)', [id, m.seq, m.def, time, fields, dev])
      else await db.query('INSERT INTO fit.messages VALUES ($1,$2,$3,$4,$5,$6,$7)', [id, m.seq, m.def, m.global, time, fields, dev])
    }
    await db.query('COMMIT')
    return id
  } catch (error) {
    await db.query('ROLLBACK')
    throw error
  }
}

export async function loadFile(db: Db, id: number): Promise<FitFile> {
  const head = (await db.query('SELECT protocol_version, profile_number FROM fit.files WHERE id = $1', [id])).rows[0]
  if (!head) throw new Error(`no fit.files row ${id}`)
  const file: FitFile = { protocol: head.protocol_version, profile: head.profile_number, definitions: [], messages: [] }
  for (const r of (await db.query('SELECT def_id, global_num, arch, fields::text AS fields, dev_fields::text AS dev FROM fit.definitions WHERE file_id = $1 ORDER BY def_id', [id])).rows)
    file.definitions.push({ id: r.def_id, global: r.global_num, arch: r.arch, fields: parseJson(r.fields), dev: parseJson(r.dev) })
  const rows = (await db.query(
    `SELECT seq, def_id, global_num, fields::text AS fields, dev::text AS dev FROM (
       SELECT seq, def_id, global_num, fields, dev FROM fit.messages WHERE file_id = $1
       UNION ALL SELECT seq, def_id, 20, fields, dev FROM fit.records WHERE file_id = $1) m ORDER BY seq`, [id])).rows
  for (const r of rows)
    file.messages.push({ seq: r.seq, def: r.def_id, global: r.global_num,
      f: new Map(Object.entries(parseJson(r.fields) as Record<string, Value>).map(([k, v]) => [Number(k), v])),
      d: new Map(Object.entries(parseJson(r.dev) as Record<string, Value>)) })
  return file
}

export async function exportFile(db: Db, id: number): Promise<Uint8Array> {
  return encode(await loadFile(db, id))
}
