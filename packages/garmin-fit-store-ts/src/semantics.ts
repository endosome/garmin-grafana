// Raw field values to scaled, named values by field number, from the profile document: enum names, scale and offset,
// date_time, and component expansion with accumulation. Sub-field selection is not ported (the Python layer has it).
import type { Field, ProfileDocument } from './profile.ts'
import type { Value } from './codec.ts'

const NUMERIC = new Set(['sint8', 'uint8', 'sint16', 'uint16', 'sint32', 'uint32', 'float32', 'float64', 'uint8z', 'uint16z', 'uint32z', 'byte', 'sint64', 'uint64', 'uint64z'])
const WIDTH: Record<string, number> = { enum: 8, sint8: 8, uint8: 8, sint16: 16, uint16: 16, sint32: 32, uint32: 32, uint8z: 8, uint16z: 16, uint32z: 32, byte: 8 }
const INVALID: Record<string, number> = { enum: 0xff, sint8: 0x7f, uint8: 0xff, sint16: 0x7fff, uint16: 0xffff, sint32: 0x7fffffff, uint32: 0xffffffff, uint8z: 0, uint16z: 0, uint32z: 0, byte: 0xff }
const FIT_EPOCH_MS = Date.UTC(1989, 11, 31)

export type Named = Map<number, Value | Date | null | Array<Value | Date | null>>

export class Semantics {
  private accumulated = new Map<string, { value: number; last: number }>()
  private profile: ProfileDocument
  constructor(profile: ProfileDocument) { this.profile = profile }

  named(message: number, fields: Map<number, Value>, expandComponents = true): Named {
    const definition = this.profile.messages[String(message)]
    const out: Named = new Map()
    const expand: number[] = []
    for (const [number, raw] of fields) {
      const field = definition?.fields[String(number)]
      if (!field) { out.set(number, raw as never); continue }
      if (field.accumulate && typeof raw === 'number') this.accumulated.set(`${message}:${number}`, { value: raw, last: raw })
      if (field.components.length) expand.push(number)
      out.set(number, this.value(field, raw))
    }
    if (!expandComponents || !definition) return out
    const shown = new Map<number, Array<number | null>>()
    const queue = [...expand]
    while (queue.length) {
      const number = queue.pop()!
      const field = definition.fields[String(number)]
      const raw = fields.get(number) ?? shownRaw(shown, number)
      const width = WIDTH[field.type]
      if (width === undefined || raw === undefined || raw === null) continue
      const elements = (Array.isArray(raw) ? raw : [raw]) as number[]
      if (elements.every((e) => e === INVALID[field.type])) continue
      let bits = 0n
      elements.forEach((e, i) => { bits |= (BigInt(e) & ((1n << BigInt(width)) - 1n)) << BigInt(i * width) })
      let available = width * elements.length
      field.components.forEach((target, i) => {
        if (available < field.bits[i]) return
        const dest = definition.fields[String(target)]
        let value = Number(bits & ((1n << BigInt(field.bits[i])) - 1n))
        bits >>= BigInt(field.bits[i]); available -= field.bits[i]
        if (dest.accumulate) {
          const key = `${message}:${target}`
          const state = this.accumulated.get(key) ?? { value, last: value }
          state.value += (value - state.last) & ((2 ** field.bits[i]) - 1)
          state.last = value
          this.accumulated.set(key, state)
          value = state.value
        }
        value = value / field.scale[i] - field.offset[i]
        const rawDest = Math.trunc((value + dest.offset[0]) * dest.scale[0])
        const list = shown.get(target) ?? []
        list.push(rawDest === (INVALID[dest.type] ?? 0xff) ? null : value)
        shown.set(target, list)
        if (dest.components.length) queue.push(target)
      })
    }
    for (const [target, list] of shown) out.set(target, (list.length === 1 ? list[0] : list) as never)
    return out
  }

  private value(field: Field, raw: Value): Value | Date | null | Array<Value | Date | null> {
    if (Array.isArray(raw) && field.type in INVALID && field.base_type !== 'byte') return (raw as number[]).map((v) => (v === INVALID[field.type] ? null : this.value(field, v)) as never)
    if (field.type === 'dateTime' && typeof raw === 'number') return new Date(FIT_EPOCH_MS + raw * 1000)
    const names = this.profile.types[field.type]
    if (names && typeof raw === 'number' && field.type !== 'localDateTime') return names[String(raw)] ?? raw
    if (NUMERIC.has(field.type) && typeof raw === 'number' && field.scale.length === 1) return (field.scale[0] === 1 ? raw : raw / field.scale[0]) - field.offset[0]
    return raw
  }
}

function shownRaw(shown: Map<number, Array<number | null>>, number: number) {
  const list = shown.get(number)
  return list && (list.length === 1 ? list[0] : list)
}
