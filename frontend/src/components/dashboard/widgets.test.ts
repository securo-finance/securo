import { describe, expect, it } from 'vitest'

import { DEFAULT_LAYOUT, WIDGET_IDS, layoutRows, moveWidget, resolveLayout } from '@/components/dashboard/widgets'

describe('resolveLayout', () => {
  it('falls back to the default layout when nothing is saved', () => {
    expect(resolveLayout(undefined)).toEqual(DEFAULT_LAYOUT)
    expect(resolveLayout(null)).toEqual(DEFAULT_LAYOUT)
    expect(resolveLayout('garbage')).toEqual(DEFAULT_LAYOUT)
  })

  it('keeps the saved order and appends widgets the layout predates', () => {
    const layout = resolveLayout({ order: ['transactions', 'hero'], hidden: [] })
    expect(layout.order.slice(0, 2)).toEqual(['transactions', 'hero'])
    expect([...layout.order].sort()).toEqual([...WIDGET_IDS].sort())
  })

  it('drops unknown and duplicate ids', () => {
    const layout = resolveLayout({
      order: ['goals', 'removed-widget', 'goals', 42],
      hidden: ['goals', 'goals', 'nope'],
    })
    expect(layout.order.filter((id) => id === 'goals')).toHaveLength(1)
    expect(layout.order).not.toContain('removed-widget')
    expect(layout.order).toHaveLength(WIDGET_IDS.length)
    expect(layout.hidden).toEqual(['goals'])
  })
})

describe('layoutRows', () => {
  it('pairs adjacent half-width widgets into one row', () => {
    expect(layoutRows(DEFAULT_LAYOUT)).toEqual([
      { kind: 'full', id: 'hero' },
      { kind: 'full', id: 'uncategorized' },
      { kind: 'half', ids: ['category-spending', 'balance-flow'] },
      { kind: 'full', id: 'goals' },
      { kind: 'full', id: 'transactions' },
    ])
  })

  it('gives a half-width widget its own row when separated or its pair is hidden', () => {
    const separated = layoutRows({
      order: ['category-spending', 'hero', 'balance-flow', 'uncategorized', 'goals', 'transactions'],
      hidden: [],
    })
    expect(separated.slice(0, 3)).toEqual([
      { kind: 'half', ids: ['category-spending'] },
      { kind: 'full', id: 'hero' },
      { kind: 'half', ids: ['balance-flow'] },
    ])

    const pairHidden = layoutRows({ ...DEFAULT_LAYOUT, hidden: ['balance-flow'] })
    expect(pairHidden).toContainEqual({ kind: 'half', ids: ['category-spending'] })
  })

  it('skips hidden widgets', () => {
    const rows = layoutRows({ ...DEFAULT_LAYOUT, hidden: ['goals', 'hero'] })
    expect(rows).not.toContainEqual({ kind: 'full', id: 'goals' })
    expect(rows).not.toContainEqual({ kind: 'full', id: 'hero' })
  })
})

describe('moveWidget', () => {
  it('swaps with the neighbour and ignores moves past either end', () => {
    const order = [...DEFAULT_LAYOUT.order]
    expect(moveWidget(order, 'uncategorized', -1).slice(0, 2)).toEqual(['uncategorized', 'hero'])
    expect(moveWidget(order, 'hero', -1)).toBe(order)
    expect(moveWidget(order, 'transactions', 1)).toBe(order)
  })
})
