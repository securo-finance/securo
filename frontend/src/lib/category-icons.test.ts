/// <reference types="node" />
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import i18next, { type Resource } from 'i18next'

import {
  CATEGORY_EMOJIS,
  CATEGORY_ICONS,
  EMOJI_TO_LUCIDE,
  extractFirstEmoji,
  getCategoryIconLabel,
  ICON_MAP,
  isEmoji,
  LUCIDE_TO_EMOJI,
} from '@/lib/category-icons'
import en from '@/locales/en.json'
import ptBR from '@/locales/pt-BR.json'

const LOCALES_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../locales',
)

function makeT(lng: string, resources: Resource, fallbackLng: string | false = false) {
  const instance = i18next.createInstance()
  instance.init({ lng, resources, fallbackLng, initAsync: false })
  return instance.t
}

describe('CATEGORY_ICONS', () => {
  it('has no duplicate names', () => {
    // A duplicate would silently win in ICON_MAP and shadow the other entry.
    const names = CATEGORY_ICONS.map((entry) => entry.name)
    expect(new Set(names).size).toBe(names.length)
  })

  it('gives every entry a name, a fallback label and a component', () => {
    for (const entry of CATEGORY_ICONS) {
      expect(entry.name, 'name').toMatch(/^[a-z0-9-]+$/)
      expect(entry.pt, `${entry.name} pt`).not.toBe('')
      expect(entry.icon, `${entry.name} icon`).toBeDefined()
    }
  })
})

describe('categoryIcons locale blocks', () => {
  it('translates every catalogue icon in every shipped locale', () => {
    // A missing key in any locale would silently fall back to the
    // Portuguese label, which is exactly what issue 880 was about.
    const names = [...new Set(CATEGORY_ICONS.map((entry) => entry.name))].sort()
    const files = readdirSync(LOCALES_DIR)
      .filter((file) => file.endsWith('.json'))
      .sort()
    expect(files.length, 'locale files').toBeGreaterThanOrEqual(2)
    for (const file of files) {
      const data = JSON.parse(readFileSync(path.join(LOCALES_DIR, file), 'utf-8'))
      const block = data.categoryIcons
      expect(block, `${file}: categoryIcons block`).toBeDefined()
      expect(Object.keys(block).sort(), `${file}: icon keys`).toEqual(names)
      for (const [key, label] of Object.entries(block)) {
        expect(String(label).trim(), `${file}: ${key}`).not.toBe('')
      }
    }
  })
})

describe('getCategoryIconLabel', () => {
  it('returns the English label under the English bundle', () => {
    const t = makeT('en', { en: { translation: en } })
    expect(getCategoryIconLabel(t, 'heart')).toBe('Health / Wellbeing')
  })

  it('returns the Portuguese label under the pt-BR bundle', () => {
    const t = makeT('pt-BR', { 'pt-BR': { translation: ptBR } })
    expect(getCategoryIconLabel(t, 'heart')).toBe('Saúde / Bem-estar')
  })

  it('uses the app fallback language when the locale has no key', () => {
    // Same fallbackLng as the app (lib/i18n.ts).
    const t = makeT('de', { de: { translation: {} }, en: { translation: en } }, 'en')
    expect(getCategoryIconLabel(t, 'heart')).toBe('Health / Wellbeing')
  })

  it('falls back to the Portuguese label when no language has the key', () => {
    const t = makeT('de', { de: { translation: {} } })
    expect(getCategoryIconLabel(t, 'heart')).toBe('Saúde / Bem-estar')
  })

  it('falls back to the raw name for an icon the catalogue does not know', () => {
    const t = makeT('en', { en: { translation: en } })
    expect(getCategoryIconLabel(t, 'not-an-icon')).toBe('not-an-icon')
  })
})

describe('ICON_MAP', () => {
  it('exposes every catalogue entry for lookup', () => {
    expect(Object.keys(ICON_MAP)).toHaveLength(CATEGORY_ICONS.length)
    for (const entry of CATEGORY_ICONS) {
      expect(ICON_MAP[entry.name]).toBe(entry.icon)
    }
  })

  it('returns nothing for a name it does not know', () => {
    // CategoryIcon relies on this being undefined so it can fall back rather
    // than render a blank box.
    expect(ICON_MAP['not-a-real-icon']).toBeUndefined()
  })
})

describe('default categories', () => {
  it('only seeds icons the catalogue can render', () => {
    // The backend seeds these names into every new workspace. One missing from
    // the catalogue shows up as the "?" fallback instead of its own icon.
    const file = path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      '../../../backend/app/data/default_categories.json',
    )
    const taxonomy = JSON.parse(readFileSync(file, 'utf-8')) as {
      groups: Record<string, { icon: string }>
      categories: Record<string, { icon: string }>
    }
    const seeded = [
      ...Object.values(taxonomy.groups),
      ...Object.values(taxonomy.categories),
    ].map((entry) => entry.icon)
    const missing = [...new Set(seeded)].filter((icon) => !(icon in ICON_MAP))
    expect(missing).toEqual([])
  })
})

describe('isEmoji', () => {
  it('recognises the emoji stored by older category rows', () => {
    expect(isEmoji('🍔')).toBe(true)
    expect(isEmoji('🏠')).toBe(true)
    expect(isEmoji('💰')).toBe(true)
  })

  it('rejects a lucide icon name', () => {
    expect(isEmoji('shopping-cart')).toBe(false)
    expect(isEmoji('house')).toBe(false)
  })

  it('rejects an empty string', () => {
    expect(isEmoji('')).toBe(false)
  })

  it('rejects a long string that merely contains an emoji', () => {
    expect(isEmoji('food 🍔')).toBe(false)
  })

  it('recognises the emoji a phone keyboard actually sends', () => {
    // Each of these is more than two code units, which is why the old
    // length check missed them.
    expect(isEmoji('👍🏽'), 'skin tone').toBe(true)
    expect(isEmoji('🇧🇷'), 'flag').toBe(true)
    expect(isEmoji('1️⃣'), 'keycap').toBe(true)
    expect(isEmoji('👨‍👩‍👧‍👦'), 'ZWJ family').toBe(true)
    expect(isEmoji('❤️'), 'text-presentation base with VS16').toBe(true)
  })

  it('rejects digits, which the unicode Emoji property also covers', () => {
    // "12" used to pass the length check and the Emoji test, so a stored
    // number was drawn as if it were a glyph.
    expect(isEmoji('12')).toBe(false)
  })
})

describe('extractFirstEmoji', () => {
  it('returns the whole sequence, not its first code unit', () => {
    expect(extractFirstEmoji('👨‍👩‍👧‍👦')).toBe('👨‍👩‍👧‍👦')
    expect(extractFirstEmoji('🇧🇷')).toBe('🇧🇷')
  })

  it('finds an emoji inside pasted text', () => {
    expect(extractFirstEmoji('party 🎉')).toBe('🎉')
    expect(extractFirstEmoji('  🦄  ')).toBe('🦄')
  })

  it('returns null when there is no emoji', () => {
    expect(extractFirstEmoji('car')).toBeNull()
    expect(extractFirstEmoji('')).toBeNull()
  })
})

describe('LUCIDE_TO_EMOJI', () => {
  it('gives every catalogue icon an emoji equivalent', () => {
    const missing = CATEGORY_ICONS.filter((entry) => !LUCIDE_TO_EMOJI[entry.name])
    expect(missing.map((entry) => entry.name)).toEqual([])
  })

  it('holds no emoji for a name the catalogue does not know', () => {
    const names = new Set(CATEGORY_ICONS.map((entry) => entry.name))
    expect(Object.keys(LUCIDE_TO_EMOJI).filter((name) => !names.has(name))).toEqual([])
  })

  it('maps one icon to one emoji, and every emoji is a single glyph', () => {
    const emojis = Object.values(LUCIDE_TO_EMOJI)
    expect(new Set(emojis).size, 'duplicate emoji').toBe(emojis.length)
    for (const emoji of emojis) expect(isEmoji(emoji), emoji).toBe(true)
  })

  it('is reversible through EMOJI_TO_LUCIDE', () => {
    for (const [name, emoji] of Object.entries(LUCIDE_TO_EMOJI)) {
      expect(EMOJI_TO_LUCIDE[emoji]).toBe(name)
    }
  })
})

describe('CATEGORY_EMOJIS', () => {
  it('offers every catalogue icon as an emoji, plus the extra presets', () => {
    const fromIcons = CATEGORY_EMOJIS.filter((entry) => entry.icon !== null)
    expect(fromIcons).toHaveLength(CATEGORY_ICONS.length)
    expect(CATEGORY_EMOJIS.length).toBeGreaterThan(CATEGORY_ICONS.length)
  })

  it('never repeats an emoji', () => {
    const emojis = CATEGORY_EMOJIS.map((entry) => entry.emoji)
    expect(new Set(emojis).size).toBe(emojis.length)
  })

  it('holds only whole emoji', () => {
    for (const entry of CATEGORY_EMOJIS) expect(isEmoji(entry.emoji), entry.emoji).toBe(true)
  })

  it('gives every extra preset the search words that make it findable', () => {
    for (const entry of CATEGORY_EMOJIS.filter((item) => item.icon === null)) {
      expect(entry.keywords.length, entry.emoji).toBeGreaterThan(0)
    }
  })
})
