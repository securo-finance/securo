import type { DashboardWidgetId } from '@/lib/dashboard-layout'

// Re-exported so registry consumers can import the id union from either
// module; it is single-homed in `@/lib/dashboard-layout` (§4.1) so the pure
// layout functions never import from components.
export type { DashboardWidgetId }

/**
 * Dashboard widget registry.
 *
 * Lives outside the page so the composition can be tested without mounting
 * it, mirroring the `nav-items.ts` precedent. Data only: no React beyond the
 * type import, no component mapping (that lives in `widget-components.tsx`),
 * no i18n calls — `translationKey` is resolved by consumers.
 *
 * Invariants (pinned by `widgets.test.ts`):
 * - every `DashboardWidgetId` appears exactly once,
 * - `defaultOrder` is a contiguous 0..n-1 permutation,
 * - `defaultVisible: true` for all six, so the pre-customization dashboard
 *   is exactly today's fixed composition (AC-5).
 */
export interface DashboardWidgetDefinition {
  id: DashboardWidgetId
  /** i18n key under `dashboard.widgets.*` (§10.1). */
  translationKey: string
  /** 0..5, unique, contiguous. */
  defaultOrder: number
  defaultVisible: boolean
}

export const DASHBOARD_WIDGETS: readonly DashboardWidgetDefinition[] = [
  { id: 'hero-balance',         translationKey: 'dashboard.widgets.heroBalance',         defaultOrder: 0, defaultVisible: true },
  { id: 'uncategorized-banner', translationKey: 'dashboard.widgets.uncategorizedBanner', defaultOrder: 1, defaultVisible: true },
  { id: 'spending-charts',      translationKey: 'dashboard.widgets.spendingCharts',      defaultOrder: 2, defaultVisible: true },
  { id: 'balance-evolution',    translationKey: 'dashboard.widgets.balanceEvolution',    defaultOrder: 3, defaultVisible: true },
  { id: 'goals-progress',       translationKey: 'dashboard.widgets.goalsProgress',       defaultOrder: 4, defaultVisible: true },
  { id: 'period-transactions',  translationKey: 'dashboard.widgets.periodTransactions',  defaultOrder: 5, defaultVisible: true },
]

/** Registry ids in declaration order. */
export const DASHBOARD_WIDGET_IDS: readonly DashboardWidgetId[] =
  DASHBOARD_WIDGETS.map((w) => w.id)

/** Fast id → definition lookup for render paths. */
export const dashboardWidgetById: Record<DashboardWidgetId, DashboardWidgetDefinition> =
  Object.fromEntries(
    DASHBOARD_WIDGETS.map((w) => [w.id, w]),
  ) as Record<DashboardWidgetId, DashboardWidgetDefinition>

export function getWidgetDefinition(
  id: DashboardWidgetId,
): DashboardWidgetDefinition {
  return dashboardWidgetById[id]
}
