/// <reference types="node" />
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it } from 'vitest'

import i18n from '@/lib/i18n'

const LOCALES_DIR = path.dirname(fileURLToPath(import.meta.url))
const FORK_DIR = path.join(LOCALES_DIR, 'fork')
const FORK_LOCALES = readdirSync(FORK_DIR)
  .filter((f) => f.endsWith('.json'))
  .map((f) => f.replace(/\.json$/, ''))
const PLURAL = /_(zero|one|two|few|many|other)$/

function flatten(obj: Record<string, unknown>, prefix = ''): Map<string, string> {
  const out = new Map<string, string>()
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'string') out.set(key, v)
    else for (const entry of flatten(v as Record<string, unknown>, key)) out.set(...entry)
  }
  return out
}

function read(file: string): Map<string, string> {
  return flatten(JSON.parse(readFileSync(file, 'utf-8')))
}

const forkEn = read(path.join(FORK_DIR, 'en.json'))
const pluralBases = new Set([...forkEn.keys()].filter((k) => PLURAL.test(k)).map((k) => k.replace(PLURAL, '')))

function placeholders(value: string): string[] {
  return [...new Set(value.match(/\{\{\w+\}\}/g) ?? [])].sort()
}

describe('fork locale bundles', () => {
  it.each(FORK_LOCALES)('%s has only keys from fork/en.json, with its placeholders', (locale) => {
    for (const [key, value] of read(path.join(FORK_DIR, `${locale}.json`))) {
      const base = key.replace(PLURAL, '')
      if (PLURAL.test(key) && pluralBases.has(base)) {
        const english = forkEn.get(`${base}_other`) ?? ''
        expect(
          placeholders(value).filter((p) => p !== '{{count}}'),
          key,
        ).toEqual(placeholders(english).filter((p) => p !== '{{count}}'))
        continue
      }
      expect(forkEn.has(key), `${locale}: ${key} is not in fork/en.json`).toBe(true)
      expect(placeholders(value), key).toEqual(placeholders(forkEn.get(key) ?? ''))
    }
  })

  it('pt-BR translates every fork string', () => {
    expect([...forkEn.keys()].filter((k) => !read(path.join(FORK_DIR, 'pt-BR.json')).has(k))).toEqual([])
  })

  it('never shadows an upstream string', () => {
    const upstream = read(path.join(LOCALES_DIR, 'en.json'))

    expect([...forkEn.keys()].filter((k) => upstream.has(k))).toEqual([])
  })
})

describe('fork strings in the running i18n instance', () => {
  afterEach(async () => {
    await i18n.changeLanguage('en')
  })

  it('serves fork strings next to upstream ones in the bundled languages', async () => {
    expect(i18n.t('spendingPlan.title')).toBe('Daily plan')
    expect(i18n.t('recurring.payNow')).toBe('Pay now')
    await i18n.changeLanguage('pt-BR')

    expect(i18n.t('recurring.payNow')).toBe('Pagar agora')
    expect(i18n.t('nav.spendingPlan')).toBe('Plano diário')
  })

  it('merges a lazily loaded language with its fork bundle', async () => {
    await i18n.changeLanguage('de')

    expect(i18n.t('recurring.payNow')).toBe('Jetzt bezahlen')
    expect(i18n.t('spendingPlan.title')).toBe('Tagesplan')
  })
})
