import type { LucideIcon } from 'lucide-react'
import { CalendarClock, CalendarRange, ChartColumnBig, ChartPie, ChartSpline, CreditCard, Gauge, LayoutList, Network, PiggyBank, Scale, Sparkles, Tags, Target, TrendingUp, Wallet } from 'lucide-react'

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

/** Widths a widget can take, in columns of a 12-column grid: a third, a half, two thirds, the whole row. */
export const SPANS = [4, 6, 8, 12] as const
export type Span = (typeof SPANS)[number]

export interface WidgetEntry extends LibraryEntry {
  /** Its width until someone resizes it. */
  span: Span
  /** The narrowest it still reads well at. */
  minSpan: Span
  /** Charts need height to be read; a row holding one is at least this tall. */
  tall?: boolean
  /** Left out of Securo's default arrangement; added from the library. */
  optional?: boolean
  /** Only offered when this part of Securo is available. */
  requires?: 'assistant'
}

/** One item placed on a surface, as saved. */
export interface LayoutItem {
  id: string
  span?: Span
}

export const DASHBOARD_WIDGETS: WidgetEntry[] = [
  { id: 'overview', titleKey: 'library.widget.overview', descriptionKey: 'library.widget.overviewHint', icon: Wallet, span: 12, minSpan: 6 },
  { id: 'uncategorized', titleKey: 'library.widget.uncategorized', descriptionKey: 'library.widget.uncategorizedHint', icon: Tags, span: 12, minSpan: 6 },
  { id: 'spending-by-category', titleKey: 'dashboard.spendingByCategory', descriptionKey: 'library.widget.spendingByCategoryHint', icon: ChartColumnBig, span: 6, minSpan: 4, tall: true },
  { id: 'balance-flow', titleKey: 'dashboard.balanceFlow', descriptionKey: 'library.widget.balanceFlowHint', icon: ChartSpline, span: 6, minSpan: 6, tall: true },
  { id: 'goals', titleKey: 'goals.dashboardTitle', descriptionKey: 'library.widget.goalsHint', icon: Target, span: 12, minSpan: 4 },
  { id: 'transactions', titleKey: 'dashboard.periodTransactions', descriptionKey: 'library.widget.transactionsHint', icon: LayoutList, span: 12, minSpan: 6 },
  { id: 'spending-pace', titleKey: 'widgets.spendingPace.title', descriptionKey: 'widgets.spendingPace.hint', icon: Gauge, span: 4, minSpan: 4, optional: true },
  { id: 'spending-distribution', titleKey: 'widgets.distribution.title', descriptionKey: 'widgets.distribution.hint', icon: ChartPie, span: 8, minSpan: 6, optional: true },
  { id: 'month-result', titleKey: 'widgets.monthResult.title', descriptionKey: 'widgets.monthResult.hint', icon: Scale, span: 4, minSpan: 4, optional: true },
  { id: 'upcoming', titleKey: 'widgets.upcoming.title', descriptionKey: 'widgets.upcoming.hint', icon: CalendarClock, span: 4, minSpan: 4, optional: true },
  { id: 'credit-cards', titleKey: 'widgets.cards.title', descriptionKey: 'widgets.cards.hint', icon: CreditCard, span: 4, minSpan: 4, optional: true },
  { id: 'assistant', titleKey: 'widgets.assistant.title', descriptionKey: 'widgets.assistant.libraryHint', icon: Sparkles, span: 6, minSpan: 4, optional: true, requires: 'assistant' },
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

/** Securo's arrangement when someone hasn't made their own: its core widgets, in order, at their own width. */
export function defaultLayout(surface: Surface): LayoutItem[] {
  return SURFACE_ENTRIES[surface].filter((entry) => !(entry as WidgetEntry).optional).map((entry) => ({ id: entry.id }))
}

/**
 * A saved arrangement, minus items this version doesn't know (a removed
 * plugin, an old widget) and widths that aren't one of the four.
 */
export function resolveLayout(surface: Surface, saved: LayoutItem[] | null | undefined): LayoutItem[] {
  if (saved == null) return defaultLayout(surface)
  const known = new Set(SURFACE_ENTRIES[surface].map((entry) => entry.id))
  return saved
    .filter((item) => known.has(item.id))
    .map((item) => (item.span !== undefined && SPANS.includes(item.span) ? item : { id: item.id }))
}

/** How wide a widget is: its saved width, never below its minimum, else its default. */
export function widgetSpan(item: LayoutItem, entry: WidgetEntry): Span {
  const span = item.span ?? entry.span
  return span < entry.minSpan ? entry.minSpan : span
}

/** The allowed width closest to `columns`, no narrower than `min`. */
export function snapSpan(columns: number, min: Span): Span {
  const allowed = SPANS.filter((span) => span >= min)
  return allowed.reduce((best, span) => (Math.abs(span - columns) < Math.abs(best - columns) ? span : best), allowed[0])
}

/**
 * `items` with one widget resized. When that widget shares a full row with
 * the one after it, the edge between them works as a divider: the neighbour
 * takes the rest of the row, so one grows as the other shrinks. Otherwise,
 * or when the neighbour can't get that narrow, only the widget changes.
 * A widget at its own default width is saved as just its id.
 */
export function resizeItem(items: LayoutItem[], entries: WidgetEntry[], id: string, span: Span): LayoutItem[] {
  const byId = new Map(entries.map((entry) => [entry.id, entry]))
  const spanOf = (item: LayoutItem) => {
    const entry = byId.get(item.id)
    return entry ? widgetSpan(item, entry) : 12
  }
  const at = (item: LayoutItem, next: Span): LayoutItem =>
    next === byId.get(item.id)?.span ? { id: item.id } : { id: item.id, span: next }

  // Place the items as the grid does, left to right, a new row when one doesn't fit.
  const rows: number[] = []
  let row = 0
  let used = 0
  for (const item of items) {
    const width = spanOf(item)
    if (used + width > 12) {
      row += 1
      used = 0
    }
    rows.push(row)
    used += width
  }

  const index = items.findIndex((item) => item.id === id)
  if (index < 0) return items
  const next = items[index + 1]
  const shareRow =
    next !== undefined &&
    rows[index + 1] === rows[index] &&
    rows.filter((r) => r === rows[index]).length === 2 &&
    spanOf(items[index]) + spanOf(next) === 12
  const rest = 12 - span
  const nextEntry = next ? byId.get(next.id) : undefined
  const neighbour =
    shareRow && nextEntry && SPANS.includes(rest as Span) && rest >= nextEntry.minSpan ? (rest as Span) : null

  return items.map((item, i) => {
    if (i === index) return at(item, span)
    if (i === index + 1 && neighbour !== null) return at(item, neighbour)
    return item
  })
}

export const SURFACE_KIND: Record<Surface, LibraryKind> = { dashboard: 'widgets', reports: 'reports' }
export const KIND_SURFACE: Partial<Record<LibraryKind, Surface>> = { widgets: 'dashboard', reports: 'reports' }

