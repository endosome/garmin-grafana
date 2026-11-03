// The profile document of the Python package, built from @garmin/fitsdk's Profile at install or test time (plan A5, R14):
// with the SDK's own camelCase names (the Python document says heart_rate where this one says heartRate; the two layers are
// compared by field number), and scale / offset / components as lists.
export interface Field {
  number: number; name: string; type: string; base_type: string; array: boolean; scale: number[]; offset: number[]
  units: string | string[]; bits: number[]; components: number[]; accumulate: boolean; sub_fields: SubField[]
}
export interface SubField {
  name: string; type: string; scale: number[]; offset: number[]; units: string | string[]; bits: number[]; components: number[]
  map: { name: string; raw_value: number }[]
}
export interface ProfileDocument {
  version: string
  types: Record<string, Record<string, string>>
  messages: Record<string, { name: string; fields: Record<string, Field> }>
}

const list = <T>(v: T | T[] | undefined, fallback: T): T[] => (Array.isArray(v) ? v : v === undefined ? [fallback] : [v])

export function buildProfile(sdk: { version: { major: number; minor: number; patch: number }; messages: any; types: any }): ProfileDocument {
  const types: ProfileDocument['types'] = {}
  for (const [type, values] of Object.entries<Record<string, string>>(sdk.types)) {
    types[type] = Object.fromEntries(Object.entries(values).map(([raw, name]) => [raw, String(name)]))
  }
  const messages: ProfileDocument['messages'] = {}
  for (const [num, message] of Object.entries<any>(sdk.messages)) {
    const fields: Record<string, Field> = {}
    for (const [fnum, f] of Object.entries<any>(message.fields)) {
      fields[fnum] = {
        number: f.num, name: f.name, type: f.type, base_type: f.baseType, array: Boolean(f.array),
        scale: list(f.scale, 1), offset: list(f.offset, 0), units: Array.isArray(f.units) ? f.units : f.units,
        bits: [...f.bits], components: f.components.map(Number), accumulate: Boolean(f.isAccumulated),
        sub_fields: f.subFields.map((s: any) => ({
          name: s.name, type: s.type, scale: list(s.scale, 1), offset: list(s.offset, 0), units: s.units,
          bits: [...s.bits], components: s.components.map(Number),
          map: s.map.map((m: any) => ({ name: m.name, raw_value: m.value })),
        })),
      }
    }
    messages[num] = { name: message.name, fields }
  }
  const { major, minor, patch } = sdk.version
  return { version: `${major}.${minor}.${patch}`, types, messages }
}
