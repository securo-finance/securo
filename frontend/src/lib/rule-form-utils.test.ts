import { describe, expect, it } from 'vitest'

import { isInvalidDescriptionAction, parseRulePriority } from './rule-form-utils'

describe('isInvalidDescriptionAction', () => {
  it('validates each description action independently', () => {
    expect(isInvalidDescriptionAction({ op: 'set_description', value: '' })).toBe(true)
    expect(isInvalidDescriptionAction({ op: 'set_description', value: '   ' })).toBe(true)
    expect(isInvalidDescriptionAction({ op: 'set_description', value: 'iFood' })).toBe(false)
    expect(isInvalidDescriptionAction({ op: 'set_description', value: 'x'.repeat(501) })).toBe(true)
    expect(isInvalidDescriptionAction({ op: 'append_notes', value: '' })).toBe(false)
  })
})

describe('parseRulePriority', () => {
  it('uses zero for a temporarily blank priority', () => {
    expect(parseRulePriority('')).toBe(0)
    expect(parseRulePriority('   ')).toBe(0)
  })

  it('returns finite numeric priority values', () => {
    expect(parseRulePriority('0')).toBe(0)
    expect(parseRulePriority('1')).toBe(1)
    expect(parseRulePriority('-2')).toBe(-2)
    expect(parseRulePriority('not-a-number')).toBe(0)
  })
})
