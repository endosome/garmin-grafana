// FIT bytes <-> the raw IR of the Python package (definitions and data messages with raw base-type values).
// 64-bit values are bigint when they do not fit a double; everything else is number, string or arrays of them.

export type Value = number | bigint | string | { hex: string } | Array<number | bigint | string | null>
export interface Definition { id: number; global: number; arch: number; fields: number[][]; dev: number[][] }
export interface Message { seq: number; def: number; global: number; f: Map<number, Value>; d: Map<string, Value> }
export interface FitFile { protocol: number; profile: number; definitions: Definition[]; messages: Message[] }

export class FitError extends Error {}

const TYPES: Record<number, { size: number; invalid: bigint | null }> = {
  0: { size: 1, invalid: 0xffn }, 1: { size: 1, invalid: 0x7fn }, 2: { size: 1, invalid: 0xffn },
  3: { size: 2, invalid: 0x7fffn }, 4: { size: 2, invalid: 0xffffn }, 5: { size: 4, invalid: 0x7fffffffn },
  6: { size: 4, invalid: 0xffffffffn }, 7: { size: 1, invalid: 0n }, 8: { size: 4, invalid: null }, 9: { size: 8, invalid: null },
  10: { size: 1, invalid: 0n }, 11: { size: 2, invalid: 0n }, 12: { size: 4, invalid: 0n }, 13: { size: 1, invalid: 0xffn },
  14: { size: 8, invalid: 0x7fffffffffffffffn }, 15: { size: 8, invalid: 0xffffffffffffffffn }, 16: { size: 8, invalid: 0n },
}
const SIGNED = new Set([1, 3, 5, 14])
const TIMESTAMP = 253
const BYTE = 0x0d

export function crc16(data: Uint8Array, crc = 0): number {
  const table = [0x0000, 0xcc01, 0xd801, 0x1400, 0xf001, 0x3c00, 0x2800, 0xe401, 0xa001, 0x6c00, 0x7800, 0xb401, 0x5000, 0x9c01, 0x8801, 0x4400]
  for (const byte of data) {
    for (const nibble of [byte & 0xf, byte >> 4]) {
      const tmp = table[crc & 0xf]
      crc = ((crc >> 4) & 0x0fff) ^ tmp ^ table[nibble]
    }
  }
  return crc
}

const kind = (base: number) => {
  const id = base & 0x1f
  if (!(id in TYPES)) throw new FitError(`unknown base type 0x${base.toString(16)}`)
  return id
}
const simplify = (v: bigint): number | bigint => (v >= -(2n ** 53n) && v <= 2n ** 53n ? Number(v) : v)

function readInt(view: DataView, at: number, id: number, little: boolean): bigint {
  const size = TYPES[id].size
  if (size === 8) return SIGNED.has(id) ? view.getBigInt64(at, little) : view.getBigUint64(at, little)
  if (size === 4) return BigInt(SIGNED.has(id) ? view.getInt32(at, little) : view.getUint32(at, little))
  if (size === 2) return BigInt(SIGNED.has(id) ? view.getInt16(at, little) : view.getUint16(at, little))
  return BigInt(SIGNED.has(id) ? view.getInt8(at) : view.getUint8(at))
}

function decodeValue(raw: Uint8Array, base: number, arch: number): Value | null {
  const id = kind(base)
  const little = arch === 0
  if (id === 7) {
    let end = raw.length
    while (end > 0 && raw[end - 1] === 0) end--
    if (end === 0) return null
    const trimmed = raw.subarray(0, end)
    try {
      const parts = new TextDecoder('utf-8', { fatal: true }).decode(trimmed).split('\0')
      return parts.length === 1 ? parts[0] : parts
    } catch {
      return { hex: Buffer.from(raw).toString('hex') }
    }
  }
  const { size, invalid } = TYPES[id]
  if (raw.length % size) return raw.every((b) => b === 0xff) ? null : Array.from(raw)
  const view = new DataView(raw.buffer, raw.byteOffset, raw.length)
  const out: Array<number | bigint | null> = []
  let allInvalid = true
  for (let at = 0; at < raw.length; at += size) {
    if (id === 8 || id === 9) {
      const v = id === 8 ? view.getFloat32(at, little) : view.getFloat64(at, little)
      if (Number.isNaN(v)) out.push(null)
      else { out.push(v); allInvalid = false }
    } else {
      const v = readInt(view, at, id, little)
      if (v !== invalid) allInvalid = false
      out.push(simplify(v))
    }
  }
  if (allInvalid) return null
  const arrayFix = out.map((v) => v)
  return arrayFix.length === 1 ? (arrayFix[0] as number | bigint) : (arrayFix as Value)
}

function encodeValue(value: Value | undefined, base: number, size: number, arch: number): Uint8Array {
  const id = kind(base)
  const little = arch === 0
  const { size: width, invalid } = TYPES[id]
  const filler = (n: number) => {
    const out = new Uint8Array(n)
    if (id === 7) return out
    if (id === 8 || id === 9) return out.fill(0xff)
    const view = new DataView(out.buffer)
    for (let at = 0; at + width <= n; at += width) writeInt(view, at, id, invalid as bigint, little)
    return out.fill(0xff, n - (n % width))
  }
  if (value === undefined || value === null) return filler(size)
  if (id === 7) {
    const bytes = typeof value === 'object' && !Array.isArray(value)
      ? Buffer.from((value as { hex: string }).hex, 'hex')
      : Buffer.concat([Buffer.from((Array.isArray(value) ? value : [value]).join('\0')), Buffer.from([0])])
    const out = new Uint8Array(Math.max(size, bytes.length))
    out.set(bytes)
    return out
  }
  if (size % width) {
    const out = new Uint8Array(Math.max(size, (value as number[]).length)).fill(0xff)
    out.set(value as number[])
    return out
  }
  const items = Array.isArray(value) ? value : [value]
  const count = Math.max(items.length, size / width)
  const out = new Uint8Array(count * width)
  const view = new DataView(out.buffer)
  for (let i = 0; i < count; i++) {
    const v = items[i]
    if (v === undefined || v === null || (typeof v === 'number' && Number.isNaN(v))) out.set(filler(width), i * width)
    else if (id === 8) view.setFloat32(i * width, Number(v), little)
    else if (id === 9) view.setFloat64(i * width, Number(v), little)
    else writeInt(view, i * width, id, BigInt(v as number | bigint), little)
  }
  return out
}

function writeInt(view: DataView, at: number, id: number, v: bigint, little: boolean) {
  const size = TYPES[id].size
  if (size === 8) SIGNED.has(id) ? view.setBigInt64(at, v, little) : view.setBigUint64(at, v, little)
  else if (size === 4) SIGNED.has(id) ? view.setInt32(at, Number(v), little) : view.setUint32(at, Number(v), little)
  else if (size === 2) SIGNED.has(id) ? view.setInt16(at, Number(v), little) : view.setUint16(at, Number(v), little)
  else SIGNED.has(id) ? view.setInt8(at, Number(v)) : view.setUint8(at, Number(v))
}

export function decode(data: Uint8Array, checkCrc = true): FitFile {
  const out: FitFile = { protocol: 0, profile: 0, definitions: [], messages: [] }
  const view = new DataView(data.buffer, data.byteOffset, data.length)
  let offset = 0
  let first = true
  while (offset < data.length) {
    if (!first && data.subarray(offset).every((b) => b === 0)) break
    if (data.length - offset < 12) throw new FitError('truncated header')
    const size = data[offset]
    if (size !== 12 && size !== 14) throw new FitError(`bad header size ${size}`)
    if (Buffer.from(data.subarray(offset + 8, offset + 12)).toString('latin1') !== '.FIT') throw new FitError('missing .FIT signature')
    const dataSize = view.getUint32(offset + 4, true)
    if (size === 14 && checkCrc) {
      const stored = view.getUint16(offset + 12, true)
      if (stored !== 0 && stored !== crc16(data.subarray(offset, offset + 12))) throw new FitError('header CRC mismatch')
    }
    const end = offset + size + dataSize
    if (end + 2 > data.length) throw new FitError('truncated file')
    if (checkCrc && crc16(data.subarray(offset, end)) !== view.getUint16(end, true)) throw new FitError('file CRC mismatch')
    if (first) { out.protocol = data[offset + 1]; out.profile = view.getUint16(offset + 2, true) }
    records(data.subarray(offset + size, end), out)
    offset = end + 2
    first = false
  }
  if (first) throw new FitError('empty input')
  return out
}

function addDefinition(file: FitFile, global: number, fields: number[][], dev: number[][], arch: number): Definition {
  const key = JSON.stringify([global, arch, fields, dev])
  const found = file.definitions.find((d) => JSON.stringify([d.global, d.arch, d.fields, d.dev]) === key)
  if (found) return found
  const created = { id: file.definitions.length, global, arch, fields, dev }
  file.definitions.push(created)
  return created
}

function records(body: Uint8Array, file: FitFile) {
  const local = new Map<number, Definition>()
  const withTimestamp = new Map<number, Definition>()
  const devTypes = new Map<string, number>()
  let pos = 0
  let last = 0
  const take = (n: number) => {
    if (pos + n > body.length) throw new FitError('truncated record')
    const chunk = body.subarray(pos, pos + n)
    pos += n
    return chunk
  }
  while (pos < body.length) {
    const header = take(1)[0]
    let definition: Definition | undefined
    let compressed = false
    if (header & 0x80) {
      definition = local.get((header >> 5) & 3)
      if (!definition) throw new FitError('data message for undefined local type')
      const offset = header & 0x1f
      last = (last & ~0x1f) + offset + (offset < (last & 0x1f) ? 0x20 : 0)
      if (!withTimestamp.has(definition.id)) {
        const fields = definition.fields.filter((f) => f[0] !== TIMESTAMP).concat([[TIMESTAMP, 4, 0x86]])
        withTimestamp.set(definition.id, addDefinition(file, definition.global, fields, definition.dev, definition.arch))
      }
      definition = withTimestamp.get(definition.id)
      compressed = true
    } else if (header & 0x40) {
      take(1)
      const arch = take(1)[0]
      if (arch > 1) throw new FitError(`bad architecture ${arch}`)
      const g = take(2)
      const global = new DataView(g.buffer, g.byteOffset, 2).getUint16(0, arch === 0)
      const fields: number[][] = []
      for (let n = take(1)[0]; n > 0; n--) {
        const [number, size, base] = take(3)
        kind(base)
        if (size === 0) throw new FitError('zero size field')
        fields.push([number, size, base])
      }
      const dev: number[][] = []
      if (header & 0x20) for (let n = take(1)[0]; n > 0; n--) dev.push(Array.from(take(3)))
      local.set(header & 0x0f, addDefinition(file, global, fields, dev, arch))
      continue
    } else {
      definition = local.get(header & 0x0f)
      if (!definition) throw new FitError('data message for undefined local type')
    }
    const message: Message = { seq: file.messages.length, def: definition!.id, global: definition!.global, f: new Map(), d: new Map() }
    for (const [number, size, base] of definition!.fields) {
      if (compressed && number === TIMESTAMP) continue
      const raw = take(size)
      const value = decodeValue(raw, base, definition!.arch)
      if (value !== null) message.f.set(number, value)
    }
    for (const [number, size, index] of definition!.dev) {
      const value = decodeValue(take(size), devTypes.get(`${index}:${number}`) ?? BYTE, definition!.arch)
      if (value !== null) message.d.set(`${index}:${number}`, value)
    }
    if (compressed) message.f.set(TIMESTAMP, last)
    const stamp = message.f.get(TIMESTAMP)
    if (typeof stamp === 'number') last = stamp
    if (message.global === 206) {
      const [i, n, b] = [0, 1, 2].map((k) => message.f.get(k))
      if (typeof i === 'number' && typeof n === 'number' && typeof b === 'number' && (b & 0x1f) in TYPES) devTypes.set(`${i}:${n}`, b)
    }
    file.messages.push(message)
  }
}

export function encode(file: FitFile): Uint8Array {
  const body: number[] = []
  const slots = new Map<string, number>() // layout key -> local type, least recently used first
  const devTypes = new Map<string, number>()
  const defs = new Map(file.definitions.map((d) => [d.id, d]))
  for (const m of file.messages) {
    const d = defs.get(m.def)!
    const known = new Set(d.fields.map((f) => f[0]))
    for (const k of m.f.keys()) if (!known.has(k)) throw new FitError(`message ${m.seq} has fields its definition lacks`)
    const chunks: Uint8Array[] = []
    const layout: number[][] = []
    const devLayout: number[][] = []
    for (const [number, size, base] of d.fields) {
      const chunk = encodeValue(m.f.get(number), base, size, d.arch)
      layout.push([number, Math.max(size, chunk.length), base])
      chunks.push(chunk)
    }
    for (const [number, size, index] of d.dev) {
      const chunk = encodeValue(m.d.get(`${index}:${number}`), devTypes.get(`${index}:${number}`) ?? BYTE, size, d.arch)
      devLayout.push([number, Math.max(size, chunk.length), index])
      chunks.push(chunk)
    }
    const key = JSON.stringify([d.global, d.arch, layout, devLayout])
    if (slots.has(key)) {
      const local = slots.get(key)!
      slots.delete(key)
      slots.set(key, local)
    } else {
      let local: number
      if (slots.size < 16) {
        const used = new Set(slots.values())
        local = [...Array(16).keys()].find((i) => !used.has(i))!
      } else {
        const oldest = slots.keys().next().value as string
        local = slots.get(oldest)!
        slots.delete(oldest)
      }
      slots.set(key, local)
      body.push(0x40 | (devLayout.length ? 0x20 : 0) | local, 0, d.arch, d.arch ? d.global >> 8 : d.global & 0xff, d.arch ? d.global & 0xff : d.global >> 8, layout.length)
      for (const f of layout) body.push(...f)
      if (devLayout.length) { body.push(devLayout.length); for (const f of devLayout) body.push(...f) }
    }
    body.push(slots.get(key)!)
    for (const c of chunks) body.push(...c)
    if (d.global === 206) {
      const [i, n, b] = [0, 1, 2].map((k) => m.f.get(k))
      if (typeof i === 'number' && typeof n === 'number' && typeof b === 'number') devTypes.set(`${i}:${n}`, b)
    }
  }
  const header = new Uint8Array(14)
  const hv = new DataView(header.buffer)
  hv.setUint8(0, 14); hv.setUint8(1, file.protocol); hv.setUint16(2, file.profile, true); hv.setUint32(4, body.length, true)
  header.set(Buffer.from('.FIT'), 8)
  hv.setUint16(12, crc16(header.subarray(0, 12)), true)
  const out = new Uint8Array(14 + body.length + 2)
  out.set(header); out.set(body, 14)
  new DataView(out.buffer).setUint16(14 + body.length, crc16(out.subarray(0, 14 + body.length)), true)
  return out
}

// The JSONL form is parsed with bigint for integers a double cannot hold.
const reviver = (_k: string, v: unknown, ctx?: { source: string }) =>
  typeof v === 'number' && ctx && /^-?\d+$/.test(ctx.source) && !Number.isSafeInteger(v) ? BigInt(ctx.source) : v
export const parseJson = (text: string) => JSON.parse(text, reviver as never)
export const stringifyJson = (value: unknown) =>
  JSON.stringify(value, (_k, v) => (typeof v === 'bigint' ? (JSON as unknown as { rawJSON(s: string): unknown }).rawJSON(String(v)) : v))
export function parseJsonl(text: string) {
  return text.trim().split('\n').map((line) => parseJson(line))
}

export function toRows(file: FitFile) {
  const plain = (v: unknown): unknown => (Array.isArray(v) ? v.map(plain) : v)
  return file.messages.map((m) => ({
    global: m.global,
    f: Object.fromEntries([...m.f.entries()].map(([k, v]) => [String(k), plain(v)])),
    d: Object.fromEntries([...m.d.entries()].map(([k, v]) => [k, plain(v)])),
  }))
}
