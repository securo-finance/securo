import { describe, expect, it } from 'vitest'

import { DASHBOARD_WIDGETS, defaultLayout, pluginReportEntries, pluginWidgetEntries, REPORTS, resizeItem, resolveLayout, snapSpan, SPANS, widgetSpan } from './library'
import type { InstalledPlugin } from '@/types'

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
    expect(defaultLayout('reports').map((item) => item.id)).toEqual(['net_worth', 'income_expenses', 'cash_flow', 'money_map', 'statement'])
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

  describe('plugin reports', () => {
    const plugin = {
      plugin_id: 'maria/casa',
      name: { en: 'Home' },
      reports: [
        { key: 'pets', name: { en: 'Pet spending' }, measure: 'expenses', group_by: 'month', chart: 'bar' },
        { key: 'split', name: { en: 'By category' }, measure: 'expenses', group_by: 'category', chart: 'pie' },
      ],
    } as unknown as InstalledPlugin

    it('lists each report of each installed plugin under an id of its own', () => {
      const entries = pluginReportEntries([plugin, { ...plugin, plugin_id: 'joao/x', reports: undefined } as unknown as InstalledPlugin])
      expect(entries.map((entry) => entry.id)).toEqual(['plugin:maria/casa:pets', 'plugin:maria/casa:split'])
      expect(entries[0].plugin).toEqual({ id: 'maria/casa', name: { en: 'Home' }, title: { en: 'Pet spending' }, description: undefined })
    })

    it("keeps a plugin's tab while the plugins are still being read, and drops one that's gone once they're known", () => {
      const saved = [{ id: 'net_worth' }, { id: 'plugin:maria/casa:pets' }, { id: 'plugin:gone/away:x' }]
      expect(resolveLayout('reports', saved, null).map((item) => item.id)).toEqual(saved.map((item) => item.id))
      expect(resolveLayout('reports', saved, pluginReportEntries([plugin])).map((item) => item.id)).toEqual(['net_worth', 'plugin:maria/casa:pets'])
      expect(resolveLayout('reports', saved).map((item) => item.id)).toEqual(['net_worth'])
    })
  })

  describe('plugin widgets', () => {
    const plugin = {
      plugin_id: 'maria/casa',
      name: { en: 'Home' },
      reports: [{ key: 'pets', name: { en: 'Pet spending' }, measure: 'expenses', group_by: 'month', chart: 'bar' }],
      widgets: [
        { key: 'pets', name: { en: 'Pets this year' }, measure: 'expenses', group_by: 'month', chart: 'bar', width: 'third', period: 'last_12_months' },
        { key: 'top', name: { en: 'Top payees' }, measure: 'expenses', group_by: 'payee', chart: 'table', width: 'full', period: 'month' },
        { key: 'narrow', name: { en: 'Narrow table' }, measure: 'expenses', group_by: 'payee', chart: 'table', width: 'third', period: 'month' },
        { key: 'mix', name: { en: 'Mix' }, code: true, measure: 'expenses', chart: 'bar', width: 'two_thirds', period: 'month' },
      ],
    } as unknown as InstalledPlugin

    it('lists each widget at the width it asked for, and never as a report', () => {
      const entries = pluginWidgetEntries([plugin, { ...plugin, plugin_id: 'joao/x', widgets: undefined } as unknown as InstalledPlugin])
      expect(entries.map((entry) => [entry.id, entry.span, entry.minSpan, entry.tall])).toEqual([
        ['plugin:maria/casa:pets', 4, 4, true],
        // A table needs half the row and no chart height.
        ['plugin:maria/casa:top', 12, 6, false],
        // Asked for a third, but a table starts no narrower than it reads.
        ['plugin:maria/casa:narrow', 6, 6, false],
        ['plugin:maria/casa:mix', 8, 4, true],
      ])
      expect(entries.every((entry) => entry.optional)).toBe(true)
      expect(pluginReportEntries([plugin]).map((entry) => entry.id)).toEqual(['plugin:maria/casa:pets'])
    })

    it("keeps a plugin's widget while the plugins are still being read, and drops one that's gone once they're known", () => {
      const saved = [{ id: 'overview' }, { id: 'plugin:maria/casa:top', span: 6 as const }, { id: 'plugin:gone/away:x' }]
      expect(resolveLayout('dashboard', saved, null)).toEqual(saved)
      expect(resolveLayout('dashboard', saved, pluginWidgetEntries([plugin]))).toEqual(saved.slice(0, 2))
    })
  })
})
