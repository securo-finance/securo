import { describe, expect, it } from 'vitest'

import { CATEGORY_ICONS, ICON_MAP, isEmoji, iconLabel } from '@/lib/category-icons'

// Every name the catalogue has ever offered. These strings are persisted in
// `categories.icon` and `goals.icon`, so dropping one silently degrades a
// user's saved category to the fallback placeholder.
const LEGACY_NAMES = [
  'house', 'lightbulb', 'droplets', 'flame', 'wifi', 'tv',
  'utensils-crossed', 'coffee', 'beer', 'pizza', 'salad',
  'car', 'fuel', 'bus', 'train', 'plane', 'bike',
  'shopping-cart', 'shopping-bag', 'gift', 'shirt',
  'pill', 'stethoscope', 'syringe', 'cross', 'heart', 'dumbbell',
  'gamepad-2', 'music', 'film', 'party-popper', 'smartphone',
  'credit-card', 'book-open', 'graduation-cap', 'briefcase',
  'building-2', 'landmark', 'wallet', 'banknote', 'piggy-bank',
  'trending-up', 'trending-down', 'receipt', 'arrow-left-right',
  'baby', 'dog', 'cat', 'scissors', 'wrench', 'hammer',
  'tree-pine', 'umbrella', 'globe', 'sparkles', 'circle-help',
]

describe('CATEGORY_ICONS', () => {
  it('has no duplicate names', () => {
    // A duplicate would silently win in ICON_MAP and shadow the other entry.
    const names = CATEGORY_ICONS.map((entry) => entry.name)
    expect(new Set(names).size).toBe(names.length)
  })

  it('gives every entry a name, search keywords and a component', () => {
    for (const entry of CATEGORY_ICONS) {
      expect(entry.name, 'name').toMatch(/^[a-z0-9-]+$/)
      expect(entry.keywords, `${entry.name} keywords`).not.toHaveLength(0)
      expect(entry.icon, `${entry.name} icon`).toBeDefined()
    }
  })

  it('keeps every name earlier versions saved to the database', () => {
    const names = new Set(CATEGORY_ICONS.map((entry) => entry.name))
    for (const legacy of LEGACY_NAMES) {
      expect(names.has(legacy), `${legacy} was dropped from the catalogue`).toBe(true)
    }
  })

  it('offers enough icons to cover what people actually categorise', () => {
    // The point of issue #1113: 56 icons was not enough to find the one you
    // want. A regression back to a token set should fail here.
    expect(CATEGORY_ICONS.length).toBeGreaterThanOrEqual(240)
  })

  it('keeps keywords lowercase so search can match without normalising', () => {
    for (const entry of CATEGORY_ICONS) {
      for (const keyword of entry.keywords) {
        expect(keyword, `${entry.name} keyword`).toBe(keyword.toLowerCase())
        expect(keyword.trim(), `${entry.name} keyword`).not.toBe('')
      }
    }
  })

  it('can be searched in Portuguese as well as English', () => {
    // The old catalogue carried Portuguese labels only. Both languages have to
    // reach the icon now, or this is a regression for existing users.
    const dog = CATEGORY_ICONS.find((entry) => entry.name === 'dog')
    expect(dog?.keywords).toContain('cachorro')
    expect(dog?.keywords).toContain('dog')
  })
})

describe('iconLabel', () => {
  it('turns an icon name into a readable tooltip', () => {
    expect(iconLabel('shopping-cart')).toBe('Shopping Cart')
    expect(iconLabel('dog')).toBe('Dog')
  })

  it('keeps trailing digits attached to the word they belong to', () => {
    expect(iconLabel('gamepad-2')).toBe('Gamepad 2')
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
})
