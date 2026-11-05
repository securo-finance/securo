import { describe, expect, it } from 'vitest'

import {
  DASHBOARD_WIDGETS,
  DASHBOARD_WIDGET_IDS,
  dashboardWidgetById,
  getWidgetDefinition,
} from '@/components/dashboard/widgets'
import type { DashboardWidgetId } from '@/lib/dashboard-layout'

/**
 * The exhaustive list of the public widget-id union. Kept literal on purpose:
 * adding a union member without a registry entry fails here, which is the
 * whole point of fe-A1.
 */
const ALL_WIDGET_IDS: DashboardWidgetId[] = [
  'hero-balance',
  'uncategorized-banner',
  'spending-charts',
  'balance-evolution',
  'goals-progress',
  'period-transactions',
]

describe('dashboard widget registry', () => {
  it('registry defines every DashboardWidgetId exactly once', () => {
    const ids = DASHBOARD_WIDGETS.map((w) => w.id)

    // Every union member is registered.
    for (const id of ALL_WIDGET_IDS) {
      expect(ids).toContain(id)
    }
    // Nothing outside the union is registered.
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toHaveLength(ALL_WIDGET_IDS.length)
    expect(new Set(ids)).toEqual(new Set(ALL_WIDGET_IDS))
    expect(DASHBOARD_WIDGET_IDS).toEqual(ids)
    expect(getWidgetDefinition('hero-balance')).toBe(DASHBOARD_WIDGETS[0])
    expect(dashboardWidgetById['goals-progress']).toBe(DASHBOARD_WIDGETS[4])
  })

  it('registry defaultOrder is a contiguous 0..n-1 permutation and defaultVisible matches the pre-customization dashboard', () => {
    const orders = DASHBOARD_WIDGETS.map((w) => w.defaultOrder).sort(
      (a, b) => a - b,
    )
    expect(orders).toEqual([0, 1, 2, 3, 4, 5])

    // Every section of the current fixed dashboard starts visible, so a user
    // without a saved layout sees exactly today's page (AC-5).
    for (const widget of DASHBOARD_WIDGETS) {
      expect(widget.defaultVisible).toBe(true)
      expect(widget.translationKey).toMatch(/^dashboard\.widgets\./)
    }

    // Declaration order matches defaultOrder (the composition loop relies on
    // registry order only through defaultOrder, but keeping them aligned
    // keeps the table readable).
    const declared = DASHBOARD_WIDGETS.map((w) => w.defaultOrder)
    expect(declared).toEqual([0, 1, 2, 3, 4, 5])
  })
})
