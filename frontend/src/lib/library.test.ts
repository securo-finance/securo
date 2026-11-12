import { describe, expect, it } from 'vitest'

import { DASHBOARD_WIDGETS, defaultLayout, REPORTS, resizeItem, resolveLayout, snapSpan, SPANS, widgetSpan } from './library'

const entry = (id: string) => DASHBOARD_WIDGETS.find((widget) => widget.id === id)!

describe('library', () => {
  it('has unique ids on each surface, and widgets that fit their own minimum', () => {
    for (const list of [DASHBOARD_WIDGETS, REPORTS]) {
      const ids = list.map((item) => item.id)
      expect(new Set(ids).size).toBe(ids.length)
    }
    for (const widget of DASHBOARD_WIDGETS) {
      expect(SPANS).toContain(widget.span)
      expect(widget.span).toBeGreaterThanOrEqual(widget.minSpan)
    }
  })

  it("defaults to everything Securo has, in Securo's order", () => {
    expect(defaultLayout('reports').map((item) => item.id)).toEqual(['net_worth', 'income_expenses', 'cash_flow', 'money_map'])
    expect(resolveLayout('dashboard', null)).toEqual(DASHBOARD_WIDGETS.filter((widget) => !widget.optional).map((widget) => ({ id: widget.id })))
    // New widgets wait in the library instead of appearing on everyone's dashboard.
    expect(resolveLayout('dashboard', null).map((item) => item.id)).not.toContain('spending-pace')
  })

  it('keeps a saved arrangement, minus what this version no longer has or a width it never offers', () => {
    expect(
      resolveLayout('dashboard', [
        { id: 'goals', span: 4 },
        { id: 'plugin:gone/away:widget' },
        { id: 'overview', span: 5 as never },
      ]),
    ).toEqual([{ id: 'goals', span: 4 }, { id: 'overview' }])
    // Removing everything is a choice, not the default.
    expect(resolveLayout('dashboard', [])).toEqual([])
  })

  it('reads a width, never below the widget minimum', () => {
    expect(widgetSpan({ id: 'goals' }, entry('goals'))).toBe(12)
    expect(widgetSpan({ id: 'goals', span: 4 }, entry('goals'))).toBe(4)
    expect(widgetSpan({ id: 'overview', span: 4 }, entry('overview'))).toBe(6)
  })

  it('snaps a dragged width to the closest one offered', () => {
    expect(snapSpan(5.2, 4)).toBe(6)
    expect(snapSpan(9.9, 4)).toBe(8)
    expect(snapSpan(11, 4)).toBe(12)
    expect(snapSpan(1, 4)).toBe(4)
    expect(snapSpan(3, 6)).toBe(6)
  })

  describe('resizeItem', () => {
    const row = [{ id: 'overview' }, { id: 'spending-by-category' }, { id: 'goals' }]

    it('moves the edge between two widgets sharing a row: one grows, the other shrinks', () => {
      const shared = [{ id: 'goals', span: 6 as const }, { id: 'spending-by-category' }]
      expect(resizeItem(shared, DASHBOARD_WIDGETS, 'goals', 8)).toEqual([
        { id: 'goals', span: 8 },
        { id: 'spending-by-category', span: 4 },
      ])
      // Back to its own width, each is saved as just its id.
      expect(resizeItem([{ id: 'goals', span: 8 }, { id: 'spending-by-category', span: 4 }], DASHBOARD_WIDGETS, 'goals', 6)).toEqual([
        { id: 'goals', span: 6 },
        { id: 'spending-by-category' },
      ])
    })

    it("leaves the neighbour alone when it can't get that narrow", () => {
      const shared = [{ id: 'spending-by-category' }, { id: 'balance-flow' }]
      // Balance flow needs half a row: spending widens and the chart moves down.
      expect(resizeItem(shared, DASHBOARD_WIDGETS, 'spending-by-category', 8)).toEqual([
        { id: 'spending-by-category', span: 8 },
        { id: 'balance-flow' },
      ])
    })

    it('only resizes a widget that has its row to itself', () => {
      expect(resizeItem(row, DASHBOARD_WIDGETS, 'goals', 6)).toEqual([...row.slice(0, 2), { id: 'goals', span: 6 }])
    })
  })
})
