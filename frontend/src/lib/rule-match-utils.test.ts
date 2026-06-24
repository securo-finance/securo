import { describe, expect, it } from 'vitest'

import { normalizeRuleMatchValue } from './rule-match-utils'

describe('normalizeRuleMatchValue', () => {
  it('normalizes case, whitespace, and accents for duplicate checks', () => {
    expect(normalizeRuleMatchValue(' Café ')).toBe('CAFE')
    expect(normalizeRuleMatchValue('Niño')).toBe(normalizeRuleMatchValue('nino'))
  })
})
