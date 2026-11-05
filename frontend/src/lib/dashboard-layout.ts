/**
 * Dashboard layout: the pure read/write contract for
 * `users.preferences.dashboard_layouts[workspaceId]` (design packet §4).
 *
 * No React, no imports from components — the registry arrives as a
 * parameter, so this module stays dependency-free and testable in the node
 * test project. Every read goes through `resolveDashboardLayout`, which is
 * total: it never throws, whatever the stored document looks like (AC-4).
 */

export type DashboardWidgetId =
  | 'hero-balance'
  | 'uncategorized-banner'
  | 'spending-charts'
  | 'balance-evolution'
  | 'goals-progress'
  | 'period-transactions'

export interface DashboardLayoutItem {
  id: DashboardWidgetId
  visible: boolean
  order: number
}

export interface DashboardLayout {
  version: 1
  workspaceId: string
  items: DashboardLayoutItem[]
}

/** Captured request owner; checked again immediately before auth assignment. */
export interface DashboardRequestBinding {
  isCurrent(): boolean
}

export interface CommittedDashboardLayout {
  workspaceId: string
  layout: DashboardLayout
}

export type DashboardLayoutsMap = Record<string, DashboardLayout>

/**
 * The registry shape these functions need — structurally satisfied by
 * `DASHBOARD_WIDGETS` (`components/dashboard/widgets.ts`) without this
 * module importing from components.
 */
export interface DashboardWidgetSpec {
  id: DashboardWidgetId
  defaultOrder: number
  defaultVisible: boolean
}

/** Write-side cap: registry holds 6 today; the cap absorbs future growth
 *  and hostile payloads (defense in depth — §4.2). */
export const MAX_LAYOUT_ITEMS = 64

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isCanonicalDashboardWorkspaceId(value: unknown): value is string {
  return typeof value === 'string' && value.length === 36 &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
}

function hasRequiredOwnKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
}

function hasExactOwnKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Reflect.ownKeys(value).length === keys.length && hasRequiredOwnKeys(value, keys)
}

/** Strict transport validation, not display repair. Always returns a fresh projection. */
function readStrictDashboardLayout(
  workspaceId: string,
  value: unknown,
  registry: readonly DashboardWidgetSpec[],
  mode: 'request' | 'stored' | 'committed',
  context: string,
): DashboardLayout {
  const invalid = (reason: string): never => {
    throw new Error(`${context}: ${reason}`)
  }
  if (!isCanonicalDashboardWorkspaceId(workspaceId)) invalid('invalid workspace UUID')
  if (!isPlainObject(value)) return invalid('invalid layout object')
  const layoutKeys = ['version', 'workspaceId', 'items']
  if (!hasRequiredOwnKeys(value, layoutKeys) || (mode === 'committed' && !hasExactOwnKeys(value, layoutKeys))) {
    invalid('invalid layout keys')
  }
  if (!isCanonicalDashboardWorkspaceId(value.workspaceId) || value.workspaceId !== workspaceId) {
    invalid('layout workspace does not match target')
  }
  if (value.version !== 1) invalid('invalid layout version')
  if (!Array.isArray(value.items)) return invalid('invalid layout items array')
  if (value.items.length < 1 || value.items.length > MAX_LAYOUT_ITEMS) {
    invalid('invalid layout item count')
  }

  const seenIds = new Set<DashboardWidgetId>()
  const seenOrders = new Set<number>()
  const items: DashboardLayoutItem[] = []
  for (const raw of value.items) {
    if (!isPlainObject(raw)) return invalid('invalid layout item object')
    const itemKeys = ['id', 'visible', 'order']
    // Stored items must match DashboardLayoutItem(extra="forbid"); only caller input strips extras.
    if (!hasRequiredOwnKeys(raw, itemKeys) || (mode !== 'request' && !hasExactOwnKeys(raw, itemKeys))) {
      invalid('invalid layout item keys')
    }
    const spec = registry.find((entry) => entry.id === raw.id)
    if (!spec || seenIds.has(spec.id)) return invalid('unknown or duplicate layout item ID')
    if (typeof raw.visible !== 'boolean') return invalid('invalid item visible flag')
    if (typeof raw.order !== 'number' || !Number.isInteger(raw.order) ||
        raw.order < 0 || raw.order >= value.items.length || seenOrders.has(raw.order)) {
      return invalid('invalid or duplicate item order')
    }
    seenIds.add(spec.id)
    seenOrders.add(raw.order)
    items.push({ id: spec.id, visible: raw.visible, order: raw.order })
  }
  // Unique integer orders in [0, n) already establish the complete set 0..n-1.
  if (!items.some((item) => item.visible)) invalid('layout must have a visible item')
  return { version: 1, workspaceId, items }
}

/** Reject malformed reserved values; strip only non-reserved caller extras. */
export function validateDashboardLayoutRequest(
  workspaceId: string,
  value: unknown,
  registry: readonly DashboardWidgetSpec[],
): DashboardLayout {
  return readStrictDashboardLayout(workspaceId, value, registry, 'request', 'Invalid dashboard layout request')
}

/** Stored target extras survive server merges; nested item extras are a conflict. */
export function validateStoredDashboardLayout(
  workspaceId: string,
  value: unknown,
  registry: readonly DashboardWidgetSpec[],
): DashboardLayout {
  return readStrictDashboardLayout(workspaceId, value, registry, 'stored', 'Invalid stored dashboard layout')
}

/** A malformed accepted response is an unconfirmed outcome, never normalized success. */
export function validateCommittedDashboardLayout(
  workspaceId: string,
  value: unknown,
  registry: readonly DashboardWidgetSpec[],
): CommittedDashboardLayout {
  const context = 'Invalid committed dashboard layout response; outcome unconfirmed'
  if (!isPlainObject(value) || !hasExactOwnKeys(value, ['workspaceId', 'layout']) ||
      !isCanonicalDashboardWorkspaceId(value.workspaceId) || value.workspaceId !== workspaceId) {
    throw new Error(`${context}: invalid workspace envelope`)
  }
  const layout = readStrictDashboardLayout(workspaceId, value.layout, registry, 'committed', context)
  return { workspaceId, layout }
}

function specById(
  registry: readonly DashboardWidgetSpec[],
  id: DashboardWidgetId,
): DashboardWidgetSpec | undefined {
  return registry.find((spec) => spec.id === id)
}

/** Registry defaults as a layout: defaultOrder sorted, reindexed, stamped. */
function registryDefaults(
  registry: readonly DashboardWidgetSpec[],
  workspaceId: string,
): DashboardLayout {
  const items = [...registry]
    .sort(
      (a, b) =>
        a.defaultOrder - b.defaultOrder || registry.indexOf(a) - registry.indexOf(b),
    )
    .map((spec, index) => ({
      id: spec.id,
      visible: spec.defaultVisible,
      order: index,
    }))
  return { version: 1, workspaceId, items }
}

/**
 * Read-side normalization (§4.3). Pure, synchronous, total.
 *
 * 1. Shape gate — unusable documents fall back entirely to registry defaults.
 * 2. Version gate — missing version is 1; version > 1 (future client) also
 *    resolves to defaults for display (the hook marks it "foreign" and
 *    suppresses auto-persist until the user edits).
 * 3. Unknown ids dropped. 4. Duplicate ids: first occurrence wins.
 * 5. Field repair: non-boolean `visible` → defaultVisible; non-finite
 *    `order` → +∞. 6. Sort + reindex 0..n-1.
 * 7. Missing registry widgets restored at their declared default positions.
 * 8. All-hidden repair: first item forced visible.
 * 9. Self-hiding widgets are exempt — the rule counts configuration, not
 *    rendered pixels (A-6).
 */
export function resolveDashboardLayout(
  registry: readonly DashboardWidgetSpec[],
  stored: unknown,
  workspaceId = '',
): DashboardLayout {
  const defaults = () => registryDefaults(registry, workspaceId)

  // 1. Shape gate.
  if (!isPlainObject(stored)) return defaults()
  if (!Array.isArray(stored.items)) return defaults()

  // 2. Version gate: missing → 1; fractional/string/negative → structural
  //    failure → full defaults; > 1 → defaults for display (foreign).
  let version = 1
  if (stored.version !== undefined) {
    const v = stored.version
    if (typeof v !== 'number' || !Number.isInteger(v) || v <= 0) return defaults()
    version = v
  }
  if (version > 1) return defaults()

  // 3-5. Per-item normalization; unknown ids and unusable entries dropped.
  interface Entry {
    spec: DashboardWidgetSpec
    visible: boolean
    order: number
    declIndex: number
  }
  const byId = new Map<DashboardWidgetId, Entry>()
  for (const raw of stored.items) {
    if (!isPlainObject(raw)) continue
    if (typeof raw.id !== 'string') continue
    const spec = specById(registry, raw.id as DashboardWidgetId)
    if (!spec) continue
    if (byId.has(spec.id)) continue // 4. first occurrence wins
    byId.set(spec.id, {
      spec,
      // 5. field repair
      visible: typeof raw.visible === 'boolean' ? raw.visible : spec.defaultVisible,
      order:
        typeof raw.order === 'number' && Number.isFinite(raw.order)
          ? raw.order
          : Number.POSITIVE_INFINITY,
      declIndex: registry.indexOf(spec),
    })
  }

  // 6. Sort: order asc, ties by defaultOrder, then registry declaration.
  const sorted = [...byId.values()].sort(
    (a, b) =>
      a.order - b.order ||
      a.spec.defaultOrder - b.spec.defaultOrder ||
      a.declIndex - b.declIndex,
  )
  let items: DashboardLayoutItem[] = sorted.map((entry, index) => ({
    id: entry.spec.id,
    visible: entry.visible,
    order: index,
  }))

  // 7. Restore missing widgets at their declared default positions, in
  //    defaultOrder sequence. A layout equal to defaults is a fixed point.
  const present = new Set(items.map((item) => item.id))
  const missing = registry
    .filter((spec) => !present.has(spec.id))
    .sort(
      (a, b) => a.defaultOrder - b.defaultOrder || registry.indexOf(a) - registry.indexOf(b),
    )
  for (const spec of missing) {
    const insertAt = items.findIndex(
      (item) => (specById(registry, item.id)?.defaultOrder ?? -1) > spec.defaultOrder,
    )
    const entry: DashboardLayoutItem = {
      id: spec.id,
      visible: spec.defaultVisible,
      order: 0,
    }
    if (insertAt === -1) items.push(entry)
    else items.splice(insertAt, 0, entry)
  }
  items = items.map((item, index) => ({ ...item, order: index }))

  // 8. At-least-one-visible repair (hostile/corrupted payloads; the UI rule
  //    in §7.5 normally prevents this state).
  if (items.length > 0 && items.every((item) => !item.visible)) {
    items[0] = { ...items[0], visible: true }
  }

  return { version: 1, workspaceId, items }
}

/**
 * Write-side sanitization (§4.4). Emits only registry ids (first wins),
 * boolean `visible`, contiguous order 0..n-1, ≤ MAX_LAYOUT_ITEMS entries,
 * `version: 1`, and the `workspaceId` the map key carries.
 *
 * Takes the registry as a parameter for the same reason resolve does: this
 * module never imports from components. The input is the resolved draft, so
 * the persisted set is exactly the registry set and round-trips unchanged.
 */
export function toPersistedLayout(
  workspaceId: string,
  items: readonly DashboardLayoutItem[],
  registry: readonly DashboardWidgetSpec[],
): DashboardLayout {
  const seen = new Set<DashboardWidgetId>()
  const sanitized: DashboardLayoutItem[] = []
  for (const item of items) {
    if (sanitized.length >= MAX_LAYOUT_ITEMS) break
    if (!isPlainObject(item)) continue
    const spec = specById(registry, item.id)
    if (!spec) continue // never write unknown ids
    if (seen.has(spec.id)) continue
    seen.add(spec.id)
    sanitized.push({
      id: spec.id,
      visible:
        typeof item.visible === 'boolean' ? item.visible : spec.defaultVisible,
      order: sanitized.length,
    })
  }
  return { version: 1, workspaceId, items: sanitized }
}

/** Move one item between indexes (pointer drag result). Clamped at the
 *  boundaries; order reindexed 0..n-1. */
export function moveItem(
  items: readonly DashboardLayoutItem[],
  fromIndex: number,
  toIndex: number,
): DashboardLayoutItem[] {
  const n = items.length
  if (n === 0) return items.map((item) => ({ ...item }))
  const clamp = (value: number) =>
    Math.min(Math.max(Number.isFinite(value) ? value : 0, 0), n - 1)
  const from = clamp(fromIndex)
  const to = clamp(toIndex)
  const next = items.slice()
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next.map((item, index) => ({ ...item, order: index }))
}

/**
 * Toggle one item's visibility without ever changing order (draft rule;
 * fe-B9 pins it). Refuses to hide the last visible widget (§7.5) by
 * returning the input unchanged — same reference — so callers can detect
 * the refusal by identity.
 */
export function toggleItem(
  items: readonly DashboardLayoutItem[],
  id: DashboardWidgetId,
  visible: boolean,
): DashboardLayoutItem[] {
  const target = items.find((item) => item.id === id)
  if (!target) return items as DashboardLayoutItem[]
  if (target.visible === visible) return items as DashboardLayoutItem[]
  if (!visible && items.every((item) => !item.visible || item.id === id)) {
    return items as DashboardLayoutItem[] // would hide the last visible widget
  }
  return items.map((item) =>
    item.id === id ? { ...item, visible } : { ...item },
  )
}

/** Reset to registry defaults (the panel's Reset button; §7.6). */
export function resetLayout(
  registry: readonly DashboardWidgetSpec[],
  workspaceId = '',
): DashboardLayout {
  return registryDefaults(registry, workspaceId)
}
