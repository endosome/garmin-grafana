import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { Profile } from '@garmin/fitsdk'
import { decode, parseJson } from '../src/codec.ts'
import { buildProfile } from '../src/profile.ts'
import { Semantics } from '../src/semantics.ts'

const corpusDir = new URL('../../garmin-fit-store/tests/corpus/', import.meta.url)
const python = (...args: string[]) => spawnSync('uv', ['run', '--locked', '--package', 'garmin-fit-store', 'python', '-m', 'garmin_fit_store', ...args],
  { cwd: new URL('../../../', import.meta.url), encoding: 'utf8', maxBuffer: 1 << 26 })
const flat = (s: string) => s.replaceAll('_', '').toLowerCase()
const same = (ours: unknown, theirs: unknown): boolean => {
  if (ours instanceof Date) return ours.toISOString().slice(0, 19) === String(theirs).slice(0, 19)
  if (typeof ours === 'string' && typeof theirs === 'string') return flat(ours) === flat(theirs)
  if (typeof ours === 'number' && typeof theirs === 'number') return Math.abs(ours - theirs) < 1e-9 * Math.max(1, Math.abs(theirs))
  if (Array.isArray(ours) && Array.isArray(theirs)) return ours.length === theirs.length && ours.every((v, i) => same(v, theirs[i]))
  return String(ours) === String(theirs)
}

const probe = python('profile-json')
const skip = probe.status !== 0 && 'uv or the Python package is not available'
const profile = buildProfile(Profile)

test('the profile document is built from @garmin/fitsdk with the same fields as the Python one', { skip }, () => {
  const theirs = JSON.parse(probe.stdout)
  assert.equal(profile.version, theirs.version)
  for (const [num, message] of Object.entries<any>(theirs.messages)) {
    assert.deepEqual(Object.keys(profile.messages[num].fields), Object.keys(message.fields), `message ${num}`)
    for (const [fnum, f] of Object.entries<any>(message.fields)) {
      const ours = profile.messages[num].fields[fnum]
      assert.deepEqual([ours.scale, ours.offset, ours.bits, ours.components, ours.accumulate], [f.scale, f.offset, f.bits, f.components, f.accumulate], `${num}.${fnum}`)
    }
  }
})

for (const name of readdirSync(corpusDir).filter((n) => n.endsWith('.fit')).map((n) => n.slice(0, -4))) {
  test(`${name}: named values by field number equal the Python layer's`, { skip }, () => {
    const done = python('named-json', new URL(`${name}.fit`, corpusDir).pathname)
    assert.equal(done.status, 0, done.stderr)
    const expected = parseJson(done.stdout) as Record<string, unknown>[]
    const semantics = new Semantics(profile)
    const messages = decode(readFileSync(new URL(`${name}.fit`, corpusDir)), false).messages
    assert.equal(messages.length, expected.length)
    messages.forEach((m, i) => {
      const ours = semantics.named(m.global, m.f)
      for (const [key, value] of Object.entries(expected[i])) {
        if (value === null) continue
        assert.ok(ours.has(Number(key)) || !/^\d+$/.test(key) , `${name} message ${i}: field ${key} missing`)
        if (ours.has(Number(key))) assert.ok(same(ours.get(Number(key)), value), `${name} message ${i} field ${key}: ${String(ours.get(Number(key)))} vs ${String(value)}`)
      }
    })
  })
}
