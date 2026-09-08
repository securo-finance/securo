import { describe, expect, it } from 'vitest'

import type { RuleAction } from '../types'

import { isInvalidDescriptionAction, parseRulePriority, previewableActions } from './rule-form-utils'

describe('isInvalidDescriptionAction', () => {
  it('validates each description action independently', () => {
    expect(isInvalidDescriptionAction({ op: 'set_description', value: '' })).toBe(true)
    expect(isInvalidDescriptionAction({ op: 'set_description', value: '   ' })).toBe(true)
    expect(isInvalidDescriptionAction({ op: 'set_description', value: 'iFood' })).toBe(false)
    expect(isInvalidDescriptionAction({ op: 'set_description', value: 'x'.repeat(501) })).toBe(true)
    expect(isInvalidDescriptionAction({ op: 'append_notes', value: '' })).toBe(false)
  })
})

describe('previewableActions', () => {
  it('drops the half-filled rows a draft carries while it is being written', () => {
    expect(previewableActions([
      { op: 'set_category', value: '' },
      { op: 'set_payee', value: '   ' },
      { op: 'set_description', value: 'x'.repeat(501) },
    ])).toEqual([])
  })

  it('keeps completed actions, and `ignore`, which has no value', () => {
    const actions: RuleAction[] = [
      { op: 'set_category', value: 'cat-1' },
      { op: 'ignore', value: '' },
      { op: 'set_category', value: '' },
    ]
    expect(previewableActions(actions)).toEqual([actions[0], actions[1]])
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
