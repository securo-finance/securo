import { describe, expect, it } from 'vitest'

import { inlineCreateItemValue, inlineCreateName } from './inline-create'

describe('inlineCreateName', () => {
  it('offers the trimmed search text when nothing has that name', () => {
    expect(inlineCreateName('  Pet food ', ['Groceries'])).toBe('Pet food')
  })

  it('offers nothing for a blank search', () => {
    expect(inlineCreateName('   ', ['Groceries'])).toBeNull()
  })

  it('treats names that differ only in case or accents as the same', () => {
    expect(inlineCreateName('GROCERIES', ['Groceries'])).toBeNull()
    expect(inlineCreateName('saude', ['Saúde'])).toBeNull()
  })

  it('still offers a name that only partially matches', () => {
    expect(inlineCreateName('Groc', ['Groceries'])).toBe('Groc')
  })
})

describe('inlineCreateItemValue', () => {
  it('contains the search text so the picker filter keeps the row', () => {
    expect(inlineCreateItemValue('Pet food ')).toContain('Pet food ')
  })
})
