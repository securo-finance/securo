/// <reference types="node" />

import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const LOCALES_DIR = path.dirname(fileURLToPath(import.meta.url))

const FEATURE_KEYS = [
  'priceHistory', 'viewPriceHistory', 'priceChangesTitle', 'priceChangesEmpty',
  'priceChangeBadge', 'priceChangeIncrease', 'priceChangeDecrease',
  'priceChangeAmount', 'priceChangeDelta', 'priceChangeGap', 'priceChangeInsufficient',
] as const
const PLACEHOLDERS: Record<string, string[]> = {
  priceChangeAmount: ['new', 'old'], priceChangeDelta: ['absolute', 'percent'],
  priceChangeGap: ['days'],
}
const PAGE = readFileSync(path.resolve(LOCALES_DIR, '../pages/recurring.tsx'), 'utf-8')
// i18next only selects plural leaves when the consumer passes count.
const PLURAL_CALLS = new Set([...PAGE.matchAll(
  /t\(['"]recurring\.(\w+)['"],\s*\{[^}]*\bcount\s*:/g
)].map(match => match[1]))


function leaves(value: Record<string, unknown>, prefix = ''): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).flatMap(([key, item]) => {
    const full = prefix ? `${prefix}.${key}` : key
    return item !== null && typeof item === 'object' && !Array.isArray(item)
      ? Object.entries(leaves(item as Record<string, unknown>, full)) : [[full, item]]
  }))
}

function featureBase(key: string): string | undefined {
  if (!key.startsWith('recurring.')) return undefined
  const leaf = key.slice('recurring.'.length)
  return FEATURE_KEYS.find(base => leaf === base || (
    PLURAL_CALLS.has(base) && new RegExp(`^${base}_(zero|one|two|few|many|other)$`).test(leaf)
  ))
}

const locales = readdirSync(LOCALES_DIR).filter(file => file.endsWith('.json')).sort()
describe('Recurring price-history live locale contract', () => {
  for (const locale of locales) {
    const current = leaves(JSON.parse(readFileSync(path.join(LOCALES_DIR, locale), 'utf-8')))

    it(`${locale} has all eleven feature messages with exact interpolation variables`, () => {
      expect(String(current['recurring.priceChangesTitle'])).toContain('30')
      for (const key of FEATURE_KEYS) {
        expect(PAGE).toContain(`recurring.${key}`)
        const variants = Object.keys(current).filter(leaf => featureBase(leaf) === key)
        expect(variants.length, key).toBeGreaterThan(0)
        for (const variant of variants) {
          expect(typeof current[variant]).toBe('string')
          const placeholders = [...String(current[variant]).matchAll(/\{\{(\w+)\}\}/g)]
            .map(match => match[1])
          expect([...new Set(placeholders)].sort(), variant).toEqual(PLACEHOLDERS[key] ?? [])
        }
      }
    })
  }
})
