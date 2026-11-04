import { describe, expect, it } from 'vitest'

import { cn, normalizeText } from '@/lib/utils'

describe('cn', () => {
  it('joins class names', () => {
    expect(cn('a', 'b')).toBe('a b')
  })

  it('drops falsy entries', () => {
    expect(cn('a', false, null, undefined, '', 'b')).toBe('a b')
  })

  it('supports conditional objects and arrays', () => {
    expect(cn({ a: true, b: false }, ['c'])).toBe('a c')
  })

  it('lets the last conflicting Tailwind utility win', () => {
    // This is the reason cn exists: a caller className has to be able to
    // override a component's default padding rather than fight it.
    expect(cn('px-2', 'px-4')).toBe('px-4')
    expect(cn('text-sm', 'text-lg')).toBe('text-lg')
  })

  it('keeps utilities that do not conflict', () => {
    expect(cn('px-2', 'py-4')).toBe('px-2 py-4')
  })

  it('returns an empty string with no input', () => {
    expect(cn()).toBe('')
  })
})

describe('normalizeText', () => {
  it('lowercases', () => {
    expect(normalizeText('Orçamento')).toBe('orcamento')
  })

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

  it('produces a value that substring matching can use directly', () => {
    expect(normalizeText('Conta Corrente').includes(normalizeText('corrente'))).toBe(
      true,
    )
  })
})
