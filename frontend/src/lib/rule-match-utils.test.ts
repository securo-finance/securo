import { describe, expect, it } from 'vitest'

import { normalizeRuleMatchValue, ruleSearchText } from './rule-match-utils'
import type { Category, Rule } from '../types'

describe('normalizeRuleMatchValue', () => {
  it('normalizes case, whitespace, and accents for duplicate checks', () => {
    expect(normalizeRuleMatchValue(' Café ')).toBe('CAFE')
    expect(normalizeRuleMatchValue('Niño')).toBe(normalizeRuleMatchValue('nino'))
  })
})

const category: Category = {
  id: 'cat-1',
  user_id: 'user-1',
  group_id: null,
  name: 'Alimentação',
  icon: '🍔',
  color: '#F59E0B',
  is_system: false,
  is_hidden: false,
  treat_as_transfer: false,
  is_ignored: false,
}

const rule: Rule = {
  id: 'rule-1',
  user_id: 'user-1',
  name: 'Delivery',
  conditions_op: 'or',
  conditions: [
    { field: 'description', op: 'contains', value: 'IFOOD' },
    { op: 'or', conditions: [{ field: 'description', op: 'contains', value: 'Rappi' }] },
  ],
  actions: [{ op: 'set_category', value: 'cat-1' }],
  priority: 0,
  is_active: true,
}

describe('ruleSearchText', () => {
  it('covers the name, condition values inside groups, and the assigned category', () => {
    const text = ruleSearchText(rule, [category])
    expect(text).toContain('DELIVERY')
    expect(text).toContain('IFOOD')
    // Values nested in an AND/OR group are searchable too.
    expect(text).toContain('RAPPI')
    // Accents are stripped, so "alimentacao" finds it.
    expect(text).toContain('ALIMENTACAO')
  })

  it('ignores actions other than set_category and unknown category ids', () => {
    const noisy: Rule = {
      ...rule,
      actions: [{ op: 'append_notes', value: '#tag' }, { op: 'set_category', value: 'missing' }],
    }
    expect(ruleSearchText(noisy, [category])).toBe('DELIVERY IFOOD RAPPI ')
  })
})
