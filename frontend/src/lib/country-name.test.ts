import { describe, it, expect } from 'vitest'
import { countryName } from './country-name'

describe('countryName', () => {
  it('answers in the reader’s language', () => {
    expect(countryName('BR', 'pt-BR')).toBe('Brasil')
    expect(countryName('BR', 'en')).toBe('Brazil')
    expect(countryName('JP', 'pt-BR')).toBe('Japão')
    expect(countryName('JP', 'it')).toBe('Giappone')
  })

  it('gets the distinctions a hand-written table would miss', () => {
    // pt-BR and pt-PT genuinely differ here, and so do the two Chinas' names
    // across locales. This is the case for not maintaining the list ourselves.
    expect(countryName('CZ', 'pt-BR')).toBe('Tchéquia')
    expect(countryName('CZ', 'pt-PT')).toBe('Chéquia')
  })

  it('accepts a lowercase code', () => {
    expect(countryName('cl', 'en')).toBe('Chile')
  })

  it('needs no entry per country, so a new pack costs nothing', () => {
    // None of these are named anywhere in the codebase.
    for (const code of ['UY', 'PH', 'UA', 'ID', 'NZ']) {
      const name = countryName(code, 'en')
      expect(name).not.toBe(code)
      expect(name.length).toBeGreaterThan(2)
    }
  })

  it('falls back to the value for anything that is not a country code', () => {
    // `tax_jurisdiction` may hold a regime code, which no locale data names.
    for (const value of ['', 'MEI', 'B', '12']) {
      expect(countryName(value, 'en')).toBe(value)
    }
  })

  it('never throws on an odd locale tag, whatever it can make of it', () => {
    // A tag Intl cannot use resolves to the default locale rather than
    // raising, so the reader still gets a name. The guarantee this asserts is
    // that a bad tag never takes the picker down.
    for (const tag of ['not-a-locale', '', 'zz-ZZ', 'x']) {
      expect(() => countryName('BR', tag)).not.toThrow()
      expect(countryName('BR', tag).length).toBeGreaterThan(0)
    }
  })
})
