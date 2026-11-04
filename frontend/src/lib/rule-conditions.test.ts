import { describe, expect, it } from 'vitest'

import { flattenConditions, hasConditionGroups, isConditionGroup } from './rule-conditions'
import type { RuleConditionNode } from '../types'

const leaf = { field: 'description', op: 'contains', value: 'UBER' }
const group = {
  op: 'or' as const,
  conditions: [
    { field: 'description', op: 'contains', value: 'IFOOD' },
    { field: 'description', op: 'contains', value: 'RAPPI' },
  ],
}

describe('rule condition helpers', () => {
  it('tells leaves and groups apart', () => {
    expect(isConditionGroup(leaf)).toBe(false)
    expect(isConditionGroup(group)).toBe(true)
  })

  it('flattens one level of groups into their leaves', () => {
    const nodes: RuleConditionNode[] = [leaf, group]
    expect(flattenConditions(nodes).map(c => c.value)).toEqual(['UBER', 'IFOOD', 'RAPPI'])
  })

  it('leaves flat rules untouched when flattening', () => {
    expect(flattenConditions([leaf])).toEqual([leaf])
  })

  it('detects rules that mix AND and OR', () => {
    expect(hasConditionGroups([leaf])).toBe(false)
    expect(hasConditionGroups([leaf, group])).toBe(true)
  })
})
