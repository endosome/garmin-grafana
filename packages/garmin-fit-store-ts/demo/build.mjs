// Emits the browser build of the codec and store next to the page: node demo/build.mjs
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'

mkdirSync(new URL('./dist/', import.meta.url), { recursive: true })
for (const name of ['codec', 'store']) {
  const source = readFileSync(new URL(`../src/${name}.ts`, import.meta.url), 'utf8')
  const js = stripTypeScriptTypes(source, { mode: 'strip' }).replaceAll('./codec.ts', './codec.js')
  writeFileSync(new URL(`./dist/${name}.js`, import.meta.url), js)
}
