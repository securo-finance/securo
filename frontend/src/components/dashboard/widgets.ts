// Dashboard widget registry. The page renders widgets in the order the user
// saved (`user.preferences.dashboard_layout`), skipping hidden ones. Ids are
// persisted, so never rename one; add a new id instead.

export const WIDGET_IDS = [
  'hero',
  'uncategorized',
  'category-spending',
  'balance-flow',
  'goals',
  'transactions',
] as const

export type WidgetId = (typeof WIDGET_IDS)[number]

export interface DashboardLayout {
  order: WidgetId[]
  hidden: WidgetId[]
}

interface WidgetMeta {
  labelKey: string
  // Half-width widgets sit side by side when adjacent, on large screens.
  size: 'full' | 'half'
}

export const WIDGETS: Record<WidgetId, WidgetMeta> = {
  hero: { labelKey: 'dashboard.customize.widgets.hero', size: 'full' },
  uncategorized: { labelKey: 'dashboard.customize.widgets.uncategorized', size: 'full' },
  'category-spending': { labelKey: 'dashboard.customize.widgets.categorySpending', size: 'half' },
  'balance-flow': { labelKey: 'dashboard.customize.widgets.balanceFlow', size: 'half' },
  goals: { labelKey: 'dashboard.customize.widgets.goals', size: 'full' },
  transactions: { labelKey: 'dashboard.customize.widgets.transactions', size: 'full' },
}

export const DEFAULT_LAYOUT: DashboardLayout = {
  order: [...WIDGET_IDS],
  hidden: [],
}

function isWidgetId(value: unknown): value is WidgetId {
  return typeof value === 'string' && (WIDGET_IDS as readonly string[]).includes(value)
}

/**
 * Turn whatever is stored in preferences into a valid layout: unknown ids
 * (a widget since removed) are dropped, duplicates collapse, and widgets the
 * saved layout predates are appended so new ones show up for existing users.
 */
export function resolveLayout(saved: unknown): DashboardLayout {
  if (!saved || typeof saved !== 'object') return { order: [...DEFAULT_LAYOUT.order], hidden: [] }
  const raw = saved as { order?: unknown; hidden?: unknown }
  const order: WidgetId[] = []
  for (const id of Array.isArray(raw.order) ? raw.order : []) {
    if (isWidgetId(id) && !order.includes(id)) order.push(id)
  }
  for (const id of WIDGET_IDS) {
    if (!order.includes(id)) order.push(id)
  }
  const hidden: WidgetId[] = []
  for (const id of Array.isArray(raw.hidden) ? raw.hidden : []) {
    if (isWidgetId(id) && !hidden.includes(id)) hidden.push(id)
  }
  return { order, hidden }
}

export type WidgetRow = { kind: 'full'; id: WidgetId } | { kind: 'half'; ids: WidgetId[] }

/**
 * Group the visible widgets into rows: consecutive half-width widgets pair up
 * two per row; everything else gets a row of its own.
 */
export function layoutRows(layout: DashboardLayout): WidgetRow[] {
  const rows: WidgetRow[] = []
  for (const id of layout.order) {
    if (layout.hidden.includes(id)) continue
    if (WIDGETS[id].size === 'half') {
      const last = rows[rows.length - 1]
      if (last?.kind === 'half' && last.ids.length < 2) last.ids.push(id)
      else rows.push({ kind: 'half', ids: [id] })
    } else {
      rows.push({ kind: 'full', id })
    }
  }
  return rows
}

export function moveWidget(order: WidgetId[], id: WidgetId, delta: -1 | 1): WidgetId[] {
  const from = order.indexOf(id)
  const to = from + delta
  if (from < 0 || to < 0 || to >= order.length) return order
  const next = [...order]
  ;[next[from], next[to]] = [next[to], next[from]]
  return next
}
