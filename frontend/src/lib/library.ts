import type { LucideIcon } from 'lucide-react'
import { CalendarRange, ChartColumnBig, ChartSpline, LayoutList, Network, PiggyBank, Tags, Target, TrendingUp, Wallet } from 'lucide-react'

/**
 * The library: what someone can place on a surface (widgets on the
 * dashboard, reports in the reports page) or apply (themes). Securo's own
 * items are listed here; a plugin's join them under ids that can't collide
 * (`plugin:owner/name:key`).
 */
export type LibraryKind = 'widgets' | 'reports' | 'themes'
export type Surface = 'dashboard' | 'reports'

export interface LibraryEntry {
  id: string
  /** i18n keys for the name and a one-line description. */
  titleKey: string
  descriptionKey: string
  icon: LucideIcon
}

export interface WidgetEntry extends LibraryEntry {
  /** Full width, or half of a two-column row on wide screens. */
  width: 'full' | 'half'
}

export const DASHBOARD_WIDGETS: WidgetEntry[] = [
  { id: 'overview', titleKey: 'library.widget.overview', descriptionKey: 'library.widget.overviewHint', icon: Wallet, width: 'full' },
  { id: 'uncategorized', titleKey: 'library.widget.uncategorized', descriptionKey: 'library.widget.uncategorizedHint', icon: Tags, width: 'full' },
  { id: 'spending-by-category', titleKey: 'dashboard.spendingByCategory', descriptionKey: 'library.widget.spendingByCategoryHint', icon: ChartColumnBig, width: 'half' },
  { id: 'balance-flow', titleKey: 'dashboard.balanceFlow', descriptionKey: 'library.widget.balanceFlowHint', icon: ChartSpline, width: 'half' },
  { id: 'goals', titleKey: 'goals.dashboardTitle', descriptionKey: 'library.widget.goalsHint', icon: Target, width: 'full' },
  { id: 'transactions', titleKey: 'dashboard.periodTransactions', descriptionKey: 'library.widget.transactionsHint', icon: LayoutList, width: 'full' },
]

export const REPORTS: LibraryEntry[] = [
  { id: 'net_worth', titleKey: 'reports.netWorth', descriptionKey: 'library.report.netWorthHint', icon: TrendingUp },
  { id: 'income_expenses', titleKey: 'reports.incomeExpenses', descriptionKey: 'library.report.incomeExpensesHint', icon: PiggyBank },
  { id: 'cash_flow', titleKey: 'reports.cashFlow', descriptionKey: 'library.report.cashFlowHint', icon: CalendarRange },
  { id: 'money_map', titleKey: 'reports.moneyMap', descriptionKey: 'library.report.moneyMapHint', icon: Network },
]

export const SURFACE_ENTRIES: Record<Surface, LibraryEntry[]> = {
  dashboard: DASHBOARD_WIDGETS,
  reports: REPORTS,
}

/** Securo's arrangement when someone hasn't made their own: everything, in order. */
export function defaultLayout(surface: Surface): string[] {
  return SURFACE_ENTRIES[surface].map((entry) => entry.id)
}

/** A saved arrangement, minus ids this version doesn't know (a removed plugin, an old widget). */
export function resolveLayout(surface: Surface, saved: string[] | null | undefined): string[] {
  if (saved == null) return defaultLayout(surface)
  const known = new Set(SURFACE_ENTRIES[surface].map((entry) => entry.id))
  return saved.filter((id) => known.has(id))
}

/** `ids` with the item at `index` moved by `offset`, clamped to the ends. */
export function moveItem(ids: string[], index: number, offset: number): string[] {
  const target = Math.max(0, Math.min(ids.length - 1, index + offset))
  if (target === index) return ids
  const next = [...ids]
  const [item] = next.splice(index, 1)
  next.splice(target, 0, item)
  return next
}

/**
 * Which widgets get half a row: two half-width widgets next to each other
 * share one; a half-width widget with no neighbour to pair with takes the
 * whole row rather than leaving a hole beside it.
 */
export function halfWidth(ids: string[], entries: Map<string, WidgetEntry>): Set<string> {
  const halves = new Set<string>()
  for (let i = 0; i < ids.length - 1; i++) {
    if (halves.has(ids[i])) continue
    if (entries.get(ids[i])?.width === 'half' && entries.get(ids[i + 1])?.width === 'half') {
      halves.add(ids[i])
      halves.add(ids[i + 1])
      i++
    }
  }
  return halves
}

export const SURFACE_KIND: Record<Surface, LibraryKind> = { dashboard: 'widgets', reports: 'reports' }
export const KIND_SURFACE: Partial<Record<LibraryKind, Surface>> = { widgets: 'dashboard', reports: 'reports' }

