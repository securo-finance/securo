/// <reference types="node" />
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const LOCALES_DIR = path.dirname(fileURLToPath(import.meta.url))
const LOCALES = readdirSync(LOCALES_DIR)
  .filter((f: string) => f.endsWith('.json'))
  .map((f: string) => f.replace(/\.json$/, ''))

function readRaw(locale: string): string {
  return readFileSync(path.join(LOCALES_DIR, `${locale}.json`), 'utf-8')
}

function flattenKeys(obj: Record<string, unknown>, prefix = ''): string[] {
  return Object.entries(obj).flatMap(([k, v]) => {
    const full = prefix ? `${prefix}.${k}` : k
    return v !== null && typeof v === 'object' && !Array.isArray(v)
      ? flattenKeys(v as Record<string, unknown>, full)
      : [full]
  })
}

function flattenValues(obj: Record<string, unknown>, prefix = ''): Map<string, string> {
  const result = new Map<string, string>()
  for (const [k, v] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'string') result.set(full, v)
    else if (v !== null && typeof v === 'object' && !Array.isArray(v))
      for (const entry of flattenValues(v as Record<string, unknown>, full))
        result.set(entry[0], entry[1])
  }
  return result
}

function extractPlaceholders(value: string): Set<string> {
  return new Set((value.match(/\{\{(\w+)\}\}/g) ?? []))
}

// i18next plural suffixes — some languages (e.g. Polish) expand a single base
// key into _zero/_one/_two/_few/_many/_other forms instead of using one string.
const PLURAL_SUFFIXES = ['_zero', '_one', '_two', '_few', '_many', '_other']

function pluralBase(key: string): string | null {
  for (const s of PLURAL_SUFFIXES) {
    if (key.endsWith(s)) return key.slice(0, -s.length)
  }
  return null
}

function hasKeyOrPluralForms(keys: Set<string>, baseKey: string): boolean {
  return keys.has(baseKey) || PLURAL_SUFFIXES.some((s) => keys.has(`${baseKey}${s}`))
}

/**
 * Minimal JSON parser that detects duplicate keys at any nesting level.
 * JSON.parse silently discards duplicates (last-wins), so we need our own walk.
 */
function findDuplicateKeys(source: string): string[] {
  const duplicates: string[] = []
  let i = 0

  const ws = () => { while (i < source.length && ' \t\r\n'.includes(source[i])) i++ }

  const str = (): string => {
    i++ // skip "
    let s = ''
    while (i < source.length && source[i] !== '"') {
      if (source[i] === '\\') { i++; s += source[i++] }
      else s += source[i++]
    }
    i++ // skip "
    return s
  }

  const val = (keyPath: string): void => {
    ws()
    if (source[i] === '{') obj(keyPath)
    else if (source[i] === '[') arr(keyPath)
    else if (source[i] === '"') str()
    else while (i < source.length && !' \t\r\n,}]'.includes(source[i])) i++
  }

  const obj = (keyPath: string): void => {
    i++ // skip {
    ws()
    const seen = new Set<string>()
    while (i < source.length && source[i] !== '}') {
      ws()
      if (source[i] !== '"') break
      const key = str()
      const full = keyPath ? `${keyPath}.${key}` : key
      if (seen.has(key)) duplicates.push(full)
      seen.add(key)
      ws(); i++ // skip :
      val(full)
      ws()
      if (source[i] === ',') i++
      ws()
    }
    i++ // skip }
  }

  const arr = (keyPath: string): void => {
    i++ // skip [
    ws()
    let idx = 0
    while (i < source.length && source[i] !== ']') {
      val(`${keyPath}[${idx++}]`)
      ws()
      if (source[i] === ',') i++
      ws()
    }
    i++ // skip ]
  }

  ws(); val('')
  return duplicates
}

describe('i18n locale files', () => {
  describe('no duplicate keys', () => {
    for (const locale of LOCALES) {
      it(`${locale}.json`, () => {
        const duplicates = findDuplicateKeys(readRaw(locale))
        expect(duplicates, `Duplicate keys: ${duplicates.join(', ')}`).toEqual([])
      })
    }
  })

  describe('all languages contain all keys from en.json', () => {
    const enKeys = new Set(flattenKeys(JSON.parse(readRaw('en'))))

    for (const locale of LOCALES.filter((l: string) => l !== 'en')) {
      it(locale, () => {
        const keys = new Set(flattenKeys(JSON.parse(readRaw(locale))))
        // A key is covered if the locale has the key directly OR has at least one
        // i18next plural form of it (e.g. _one/_few/_many/_other for Polish).
        const missing = [...enKeys].filter((k) => !hasKeyOrPluralForms(keys, k))
        expect(missing, `Keys missing in ${locale}:`).toEqual([])
      })
    }
  })

  describe('no extra keys not present in en.json', () => {
    const enKeys = new Set(flattenKeys(JSON.parse(readRaw('en'))))

    for (const locale of LOCALES.filter((l: string) => l !== 'en')) {
      it(locale, () => {
        const keys = new Set(flattenKeys(JSON.parse(readRaw(locale))))
        // A key is valid if it exists in en directly, OR if it is a plural form
        // of a key that exists in en (e.g. "foo_few" is valid when en has "foo").
        const extra = [...keys].filter((k) => {
          if (enKeys.has(k)) return false
          const base = pluralBase(k)
          return !(base && enKeys.has(base))
        })
        expect(extra, `Extra keys in ${locale} not in en:`).toEqual([])
      })
    }
  })

  describe('placeholder variables match en.json', () => {
    const enValues = flattenValues(JSON.parse(readRaw('en')))

    for (const locale of LOCALES.filter((l: string) => l !== 'en')) {
      it(locale, () => {
        const localeValues = flattenValues(JSON.parse(readRaw(locale)))
        const mismatches: string[] = []

        for (const [key, enValue] of enValues) {
          const enPlaceholders = extractPlaceholders(enValue)
          if (enPlaceholders.size === 0) continue

          // For plural-form locales the base key won't exist — check each plural form instead.
          const keysToCheck = localeValues.has(key)
            ? [key]
            : PLURAL_SUFFIXES.map((s) => `${key}${s}`).filter((k) => localeValues.has(k))

          for (const localeKey of keysToCheck) {
            const missing = [...enPlaceholders].filter(
              (p) => !extractPlaceholders(localeValues.get(localeKey)!).has(p),
            )
            if (missing.length > 0)
              mismatches.push(`${localeKey}: missing ${missing.map((p) => `{{${p}}}`).join(', ')}`)
          }
        }

        expect(mismatches, `Placeholder mismatches in ${locale}:`).toEqual([])
      })
    }
  })
})
