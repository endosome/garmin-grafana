import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { decode, encode, parseJsonl, toRows, FitError } from '../src/codec.ts'

const corpus = new URL('../../garmin-fit-store/tests/corpus/', import.meta.url)
const names = readdirSync(corpus).filter((n) => n.endsWith('.fit')).map((n) => n.slice(0, -4))

const sorted = (o: object) => Object.fromEntries(Object.entries(o).sort())
const expectedRows = (name: string) =>
  parseJsonl(readFileSync(new URL(`${name}.jsonl`, corpus), 'utf8')).filter((l) => l.msg).map((l) => ({ global: l.msg.global, f: sorted(l.msg.f), d: sorted(l.msg.d) }))
const rows = (file: ReturnType<typeof decode>) => toRows(file).map((r) => ({ global: r.global, f: sorted(r.f), d: sorted(r.d) }))

for (const name of names) {
  test(`${name}: decodes to the expected IR`, () => {
    assert.deepEqual(rows(decode(readFileSync(new URL(`${name}.fit`, corpus)), false)), expectedRows(name))
  })
  test(`${name}: survives decode(encode(decode(x)))`, () => {
    const original = decode(readFileSync(new URL(`${name}.fit`, corpus)), false)
    assert.deepEqual(rows(decode(encode(original))), rows(original))
  })
}

test('malformed input raises FitError', () => {
  assert.throws(() => decode(new Uint8Array(0)), FitError)
  assert.throws(() => decode(new Uint8Array(20).fill(14)), FitError)
  const data = readFileSync(new URL('activity.fit', corpus))
  for (let end = 0; end < 80; end++) assert.throws(() => decode(data.subarray(0, end), false), FitError)
})
