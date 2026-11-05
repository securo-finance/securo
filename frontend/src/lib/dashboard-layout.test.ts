import { describe, expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  MAX_LAYOUT_ITEMS,
  moveItem,
  resetLayout,
  resolveDashboardLayout,
  toPersistedLayout,
  toggleItem,
  type DashboardLayout,
  type DashboardLayoutItem,
} from '@/lib/dashboard-layout'

const registry = DASHBOARD_WIDGETS

function defaults(workspaceId = ''): DashboardLayout {
  return resolveDashboardLayout(registry, null, workspaceId)
}

/** The registry set as visible items in default order. */
function defaultItems(): DashboardLayoutItem[] {
  return registry.map((spec, index) => ({
    id: spec.id,
    visible: spec.defaultVisible,
    order: index,
  }))
}

describe('resolveDashboardLayout', () => {
  it('resolveDashboardLayout returns registry defaults when nothing is stored', () => {
    const resolved = defaults('ws-1')
    expect(resolved.version).toBe(1)
    expect(resolved.workspaceId).toBe('ws-1')
    expect(resolved.items).toEqual(defaultItems())
    expect(resolved).toEqual(defaults('ws-1'))
  })

  it.each([
    ['null', null],
    ['string', 'nope'],
    ['number', 42],
    ['array', [1, 2]],
    ['empty object', {}],
    ['items not an array', { items: {} }],
    ['version not a number', { version: 'x', items: [] }],
    ['item null', { version: 1, items: [null] }],
    ['item id not a string', { version: 1, items: [{ id: 42 }] }],
    [
      'item visible not a boolean',
      { version: 1, items: [{ id: 'hero-balance', visible: 'yes' }] },
    ],
    [
      'item order NaN',
      { version: 1, items: [{ id: 'hero-balance', order: NaN }] },
    ],
  ])(
    'resolveDashboardLayout falls back to defaults on malformed documents without throwing (%s)',
    (_label, stored) => {
      expect(() => resolveDashboardLayout(registry, stored, 'ws-1')).not.toThrow()
      expect(resolveDashboardLayout(registry, stored, 'ws-1')).toEqual(defaults('ws-1'))
    },
  )

  it('resolveDashboardLayout drops unknown widget ids', () => {
    const stored = {
      version: 1,
      items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'mystery-widget', visible: true, order: 1 },
        { id: 'period-transactions', visible: true, order: 2 },
      ],
    }
    const resolved = resolveDashboardLayout(registry, stored)
    expect(resolved.items.map((i) => i.id)).not.toContain('mystery-widget')
    expect(resolved.items).toHaveLength(registry.length)
    // Known ids keep their stored relative order.
    const ids = resolved.items.map((i) => i.id)
    expect(ids.indexOf('hero-balance')).toBeLessThan(
      ids.indexOf('period-transactions'),
    )
  })

  it('resolveDashboardLayout keeps the first occurrence of duplicated ids', () => {
    const stored = {
      version: 1,
      items: [
        { id: 'goals-progress', visible: false, order: 0 },
        { id: 'hero-balance', visible: true, order: 1 },
        { id: 'goals-progress', visible: true, order: 2 },
      ],
    }
    const resolved = resolveDashboardLayout(registry, stored)
    const goals = resolved.items.filter((i) => i.id === 'goals-progress')
    expect(goals).toHaveLength(1)
    // First occurrence (hidden) wins over the later duplicate (visible).
    expect(goals[0].visible).toBe(false)
  })

  it('resolveDashboardLayout restores missing widgets at their declared default positions', () => {
    // Defaults minus the goals widget: goals must come back in its own slot
    // (between balance-evolution and period-transactions).
    const stored = {
      version: 1,
      items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'period-transactions', visible: true, order: 4 },
      ],
    }
    const resolved = resolveDashboardLayout(registry, stored)
    expect(resolved.items).toEqual(defaultItems())

    // A new widget inserted mid-defaults under a custom order keeps its
    // declared position relative to defaults, not append order.
    const custom = {
      version: 1,
      items: [
        { id: 'period-transactions', visible: true, order: 0 },
        { id: 'hero-balance', visible: true, order: 1 },
      ],
    }
    const resolved2 = resolveDashboardLayout(registry, custom)
    // Rule 7 as spec'd: each missing widget slots in before the first
    // sequence item whose defaultOrder is strictly greater — banner,
    // spending, evolution, goals all precede period-transactions (5); the
    // stored pair keeps its relative order at the end.
    expect(resolved2.items.map((i) => i.id)).toEqual([
      'uncategorized-banner',
      'spending-charts',
      'balance-evolution',
      'goals-progress',
      'period-transactions',
      'hero-balance',
    ])
    expect(resolved2.items.map((i) => i.order)).toEqual([0, 1, 2, 3, 4, 5])
  })

  it('resolveDashboardLayout treats missing version as 1 and newer versions as foreign defaults', () => {
    // Missing version → processed as 1.
    const noVersion = {
      items: [{ id: 'period-transactions', visible: false, order: 0 }],
    }
    const resolved = resolveDashboardLayout(registry, noVersion)
    expect(resolved.items.find((i) => i.id === 'period-transactions')?.visible).toBe(
      false,
    )

    // version: 1 → processed.
    const v1 = resolveDashboardLayout(registry, {
      version: 1,
      items: [{ id: 'period-transactions', visible: false, order: 0 }],
    })
    expect(v1).toEqual(resolved)

    // version > 1 (written by a future client) → registry defaults for
    // display, no throw. The hook additionally inspects the raw version to
    // mark the layout "foreign" and suppress auto-persist (fe-C6).
    for (const version of [2, 99]) {
      const foreign = resolveDashboardLayout(registry, {
        version,
        items: [{ id: 'period-transactions', visible: false, order: 0 }],
      })
      expect(foreign).toEqual(defaults())
    }
  })

  it('resolveDashboardLayout forces one widget visible when every item is hidden', () => {
    const stored = {
      version: 1,
      items: registry.map((spec) => ({
        id: spec.id,
        visible: false,
        order: spec.defaultOrder,
      })),
    }
    const resolved = resolveDashboardLayout(registry, stored)
    expect(resolved.items.filter((i) => i.visible)).toHaveLength(1)
    // Lowest order (tie-broken by defaultOrder) is forced visible.
    expect(resolved.items[0]).toEqual({
      id: 'hero-balance',
      visible: true,
      order: 0,
    })
  })
})

describe('toPersistedLayout', () => {
  it('toPersistedLayout output round-trips through resolveDashboardLayout unchanged', () => {
    const draft: DashboardLayoutItem[] = [
      { id: 'period-transactions', visible: true, order: 0 },
      { id: 'goals-progress', visible: false, order: 1 },
      { id: 'hero-balance', visible: true, order: 2 },
      { id: 'uncategorized-banner', visible: true, order: 3 },
      { id: 'spending-charts', visible: true, order: 4 },
      { id: 'balance-evolution', visible: true, order: 5 },
    ]
    const persisted = toPersistedLayout('ws-9', draft, registry)

    expect(persisted.version).toBe(1)
    expect(persisted.workspaceId).toBe('ws-9')
    // Only registry ids, first-wins on duplicates, contiguous order.
    const registryIds = new Set(registry.map((s) => s.id))
    expect(persisted.items.every((i) => registryIds.has(i.id))).toBe(true)
    expect(persisted.items.map((i) => i.order)).toEqual([0, 1, 2, 3, 4, 5])
    expect(persisted.items.every((i) => typeof i.visible === 'boolean')).toBe(true)
    expect(persisted.items.length).toBeLessThanOrEqual(MAX_LAYOUT_ITEMS)

    // Idempotence: resolving the persisted document is a fixed point.
    expect(resolveDashboardLayout(registry, persisted, 'ws-9')).toEqual(persisted)
    // And persisting again is byte-identical.
    expect(
      toPersistedLayout('ws-9', persisted.items, registry),
    ).toEqual(persisted)
  })

  it('toPersistedLayout drops unknown ids, de-duplicates, and enforces the 64-item cap', () => {
    const hostile: DashboardLayoutItem[] = [
      { id: 'not-registered' as DashboardLayoutItem['id'], visible: true, order: 0 },
      { id: 'hero-balance', visible: true, order: 1 },
      { id: 'hero-balance', visible: false, order: 2 },
      ...Array.from({ length: 100 }, (_, n) => ({
        id: 'goals-progress' as const,
        visible: true,
        order: n,
      })),
    ]
    const persisted = toPersistedLayout('ws-x', hostile, registry)
    expect(persisted.items.map((i) => i.id)).toEqual([
      'hero-balance',
      'goals-progress',
    ])
    expect(MAX_LAYOUT_ITEMS).toBe(64)
    expect(persisted.items.length).toBeLessThanOrEqual(MAX_LAYOUT_ITEMS)
  })
})

describe('moveItem / toggleItem / resetLayout', () => {
  const items: DashboardLayoutItem[] = [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'goals-progress', visible: true, order: 2 },
  ]

  it('moveItem clamps at list boundaries and toggleItem never changes order', () => {
    // Interior move reindexes.
    expect(moveItem(items, 0, 2).map((i) => i.id)).toEqual([
      'uncategorized-banner',
      'goals-progress',
      'hero-balance',
    ])
    // Out-of-range indexes clamp instead of throwing or wrapping.
    expect(moveItem(items, -5, 1).map((i) => i.id)).toEqual([
      'uncategorized-banner',
      'hero-balance',
      'goals-progress',
    ])
    expect(moveItem(items, 1, 99).map((i) => i.id)).toEqual([
      'hero-balance',
      'goals-progress',
      'uncategorized-banner',
    ])
    expect(moveItem([], 0, 0)).toEqual([])

    // Toggling visibility never reorders.
    const toggled = toggleItem(items, 'goals-progress', false)
    expect(toggled.map((i) => i.id)).toEqual(items.map((i) => i.id))
    expect(toggled.find((i) => i.id === 'goals-progress')?.visible).toBe(false)

    // Refuses to hide the last visible widget: input returned unchanged.
    const oneVisible: DashboardLayoutItem[] = [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'goals-progress', visible: false, order: 1 },
    ]
    const refused = toggleItem(oneVisible, 'hero-balance', false)
    expect(refused).toBe(oneVisible)
    expect(refused[0].visible).toBe(true)
  })

  it('resetLayout restores registry defaults for the workspace', () => {
    expect(resetLayout(registry, 'ws-3')).toEqual({
      version: 1,
      workspaceId: 'ws-3',
      items: defaultItems(),
    })
  })
})
