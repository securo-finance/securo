import { describe, it, expect } from 'vitest'
import { countryFlag } from './country-flag'

describe('countryFlag', () => {
  it('derives the flag from the country code', () => {
    expect(countryFlag('BR')).toBe('🇧🇷')
    expect(countryFlag('DE')).toBe('🇩🇪')
    expect(countryFlag('US')).toBe('🇺🇸')
  })

  it('accepts lowercase and surrounding space', () => {
    expect(countryFlag('br')).toBe('🇧🇷')
    expect(countryFlag(' gb ')).toBe('🇬🇧')
  })

  it('returns nothing for anything that is not a country code', () => {
    // `tax_jurisdiction` may hold a regime code, which has no flag. Emitting
    // one anyway would render as unrelated boxes.
    for (const value of ['', 'MEI', 'B', 'BRA', '12', 'B1']) {
      expect(countryFlag(value)).toBe('')
    }
  })

  it('needs no lookup table, so a new pack gets a flag for free', () => {
    expect(countryFlag('JP')).toBe('🇯🇵')
    expect(countryFlag('ZA')).toBe('🇿🇦')
  })
})
