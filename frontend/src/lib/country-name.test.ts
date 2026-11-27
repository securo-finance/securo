import { describe, it, expect } from 'vitest'
import { countryName } from './country-name'

describe('countryName', () => {
  it('answers in the reader’s language', () => {
    expect(countryName('BR', 'pt-BR')).toBe('Brasil')
    expect(countryName('BR', 'en')).toBe('Brazil')
    expect(countryName('JP', 'pt-BR')).toBe('Japão')
    expect(countryName('JP', 'it')).toBe('Giappone')
  })

  it('accepts a lowercase code', () => {
    expect(countryName('cl', 'en')).toBe('Chile')
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
