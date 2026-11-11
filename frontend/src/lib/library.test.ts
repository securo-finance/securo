import { describe, expect, it } from 'vitest'

import { DASHBOARD_WIDGETS, defaultLayout, halfWidth, moveItem, REPORTS, resolveLayout } from './library'

const entries = new Map(DASHBOARD_WIDGETS.map((entry) => [entry.id, entry]))

describe('library', () => {
  it('has unique ids on each surface', () => {
    for (const list of [DASHBOARD_WIDGETS, REPORTS]) {
      const ids = list.map((entry) => entry.id)
      expect(new Set(ids).size).toBe(ids.length)
    }
  })

  it("defaults to everything Securo has, in Securo's order", () => {
    expect(defaultLayout('reports')).toEqual(['net_worth', 'income_expenses', 'cash_flow', 'money_map'])
    expect(resolveLayout('dashboard', null)).toEqual(DASHBOARD_WIDGETS.map((entry) => entry.id))
  })

  it('keeps a saved arrangement, minus what this version no longer has', () => {
    expect(resolveLayout('dashboard', ['goals', 'plugin:gone/away:widget', 'overview'])).toEqual(['goals', 'overview'])
    // Removing everything is a choice, not the default.
    expect(resolveLayout('dashboard', [])).toEqual([])
  })

  it('moves an item within bounds', () => {
    expect(moveItem(['a', 'b', 'c'], 0, 1)).toEqual(['b', 'a', 'c'])
    expect(moveItem(['a', 'b', 'c'], 2, -1)).toEqual(['a', 'c', 'b'])
    const same = ['a', 'b']
    expect(moveItem(same, 0, -1)).toBe(same)
    expect(moveItem(same, 1, 1)).toBe(same)
  })

  it('pairs half-width widgets next to each other, and widens one left alone', () => {
    expect(halfWidth(['overview', 'spending-by-category', 'balance-flow'], entries)).toEqual(
      new Set(['spending-by-category', 'balance-flow']),
    )
    expect(halfWidth(['spending-by-category', 'overview', 'balance-flow'], entries)).toEqual(new Set())
  })
})
