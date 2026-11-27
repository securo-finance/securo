import { describe, expect, it } from 'vitest'

import { normalizeText } from '@/lib/utils'

describe('normalizeText', () => {
  it('strips diacritics so an unaccented search still matches', () => {
    // Typing "orcamento" has to find "Orçamento"; Brazilian users rarely
    // type the cedilla into a search box.
    expect(normalizeText('Orçamento')).toBe('orcamento')
    expect(normalizeText('Salário')).toBe('salario')
    expect(normalizeText('Café')).toBe('cafe')
    expect(normalizeText('São Paulo')).toBe('sao paulo')
  })

  it('handles other supported alphabets without dropping characters', () => {
    expect(normalizeText('Żywność')).toBe('zywnosc')
    expect(normalizeText('Ünal')).toBe('unal')
  })

  it('leaves plain ASCII untouched apart from case', () => {
    expect(normalizeText('Groceries')).toBe('groceries')
  })

  it('returns an empty string unchanged', () => {
    expect(normalizeText('')).toBe('')
  })
})
