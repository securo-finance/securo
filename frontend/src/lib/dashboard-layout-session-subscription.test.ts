import { expect, it, vi } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { toPersistedLayout, toggleItem, type DashboardLayout } from '@/lib/dashboard-layout'
import type { SessionSnapshot, SessionStore, StoreOptions } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'

// Prospective public port only; no adapter, replacement store or fallback.
type SubscriptionStore = SessionStore & { subscribe(listener: () => void): () => void }

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}

// Freeze transport fixtures only, never a snapshot to manufacture compliance.
function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}

function expectDeepFrozen(value: unknown): void {
  if (value !== null && typeof value === 'object') {
    expect(Object.isFrozen(value)).toBe(true)
    Object.values(value).forEach((child: unknown) => { expectDeepFrozen(child) })
  }
}

// Only a notification microtask checkpoint, not task/read completion or a timer.
function drainNotifications(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('publishes stable deeply frozen snapshots through one writable-owner subscription trace without any write', async () => {
  const sibling: DashboardLayout = freezeFixture({
    version: 1, workspaceId: B,
    items: [
      { id: 'hero-balance', visible: false, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  })
  const entryUser: User = freezeFixture({
    id: ACTOR, email: 'subscription-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Synthetic subscription tracer',
      onboarding_completed: true,
      dashboard_layouts: {
        [B]: sibling,
        [A]: {
          version: 1, workspaceId: A,
          items: [
            { id: 'hero-balance', visible: true, order: 0 },
            { id: 'uncategorized-banner', visible: true, order: 1 },
            { id: 'spending-charts', visible: true, order: 2 },
            { id: 'balance-evolution', visible: true, order: 3 },
            { id: 'goals-progress', visible: true, order: 4 },
            { id: 'period-transactions', visible: true, order: 5 },
          ],
        },
      },
    },
  })
  const authSeed: Readonly<User> = freezeFixture(structuredClone(entryUser))
  const seedBefore = structuredClone(authSeed)
  const preferencesBefore = authSeed.preferences
  const entryBefore = structuredClone(entryUser)
  const siblingBefore = structuredClone(sibling)
  const registryBefore = structuredClone(DASHBOARD_WIDGETS)
  const registryFrozenBefore = [Object.isFrozen(DASHBOARD_WIDGETS), ...DASHBOARD_WIDGETS.map(Object.isFrozen)]
  const entryBarrier = deferred<User>()
  const me = vi.fn<StoreOptions['api']['me']>(() => entryBarrier.promise)
  const saveLayout = vi.fn<StoreOptions['api']['saveLayout']>(() => {
    throw new Error('Forbidden scoped dashboard write in subscription tracer')
  })
  const updateMe = vi.fn<(...args: unknown[]) => Promise<User>>(() => {
    throw new Error('Forbidden whole-preferences write in subscription tracer')
  })

  vi.resetModules()
  vi.doMock('@/lib/api', () => ({ auth: { me, updateMe }, dashboard: { saveLayout } }))
  try {
    const { createDashboardLayoutSession } = await import('@/lib/dashboard-layout-session')
    const store = createDashboardLayoutSession({
      registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: 1, authSeed,
      getTokenIdentity: () => 'synthetic-subscription-boundary-token',
      getWorkspaceId: () => A, api: { me, saveLayout },
    }) as SubscriptionStore
    const initial = store.getSnapshot(A)
    // FIRST assertion: genuine reference-identity RED on the current fresh-clone getter.
    expect(store.getSnapshot(A), 'unchanged real getter must reuse its public snapshot').toBe(initial)
    const initialBefore = structuredClone(initial)
    // Missing API must fail explicitly, outside any rejection catch.
    expect(typeof store.subscribe, 'public subscribe must exist').toBe('function')
    const observed: SessionSnapshot[] = []
    const observer = vi.fn(() => { observed.push(store.getSnapshot(A)) })
    const unsubscribe = store.subscribe(observer)
    expect(typeof unsubscribe).toBe('function')
    expect(observer).not.toHaveBeenCalled()
    expect(store.getSnapshot(A)).toBe(initial)
    await drainNotifications()
    expect(observer).not.toHaveBeenCalled()

    const entryRead = store.refresh(A)
    expect(me).toHaveBeenCalledTimes(1)
    const pending = store.getSnapshot(A)
    expect(pending).not.toBe(initial)
    expect(pending).toMatchObject({ readPending: true, writable: false, state: 'closed' })
    expect(store.getSnapshot(A)).toBe(pending)
    expect(observer).not.toHaveBeenCalled()
    // The transport remains unresolved: this checkpoint can only deliver pending.
    await drainNotifications()
    expect(observed).toHaveLength(1)
    expect(observed[0]).toBe(pending)
    expectDeepFrozen(pending)
    const pendingBefore = structuredClone(pending)

    entryBarrier.resolve(entryUser)
    await entryRead
    await store.whenIdle()
    await drainNotifications()
    const completed = store.getSnapshot(A)
    expect(completed).not.toBe(pending)
    expect(store.getSnapshot(A)).toBe(completed)
    expect(completed).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A, provenance: 'saved',
      writable: true, state: 'closed', pending: false, readPending: false,
      layout: entryUser.preferences.dashboard_layouts?.[A],
      openedBaseline: null, draft: null, overlay: {},
      counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(completed.overlay).toEqual({})
    expect(observed).toHaveLength(2)
    expect(observed[1]).toBe(completed)
    expectDeepFrozen(completed)
    expect(completed.layout).not.toBe(entryUser.preferences.dashboard_layouts?.[A])
    expect(completed.layout.items).not.toBe(entryUser.preferences.dashboard_layouts?.[A]?.items)
    completed.layout.items.forEach((item, index) => {
      expect(item).not.toBe(entryUser.preferences.dashboard_layouts?.[A]?.items[index])
    })
    const completedBefore = structuredClone(completed)

    const session = store.open(A)
    const editing = store.getSnapshot(A)
    expect(editing).not.toBe(completed)
    expect(editing).toMatchObject({
      writable: true, state: 'editing', readPending: false, pending: false,
      layout: completed.layout, openedBaseline: completed.layout, draft: completed.layout,
    })
    expect(store.getSnapshot(A)).toBe(editing)
    expect(observer).toHaveBeenCalledTimes(2)
    await drainNotifications()
    expect(observed).toHaveLength(3)
    expect(observed[2]).toBe(editing)
    expectDeepFrozen(editing)
    const editingBefore = structuredClone(editing)
    const baselineBefore = structuredClone(editing.openedBaseline)
    const draftBefore = structuredClone(editing.draft)

    // S1 negative: equivalent detached content, paired with a real edit below.
    store.setDraft(session, completed.layout.items.map((item) => ({ ...item })))
    expect(store.getSnapshot(A)).toBe(editing)
    await drainNotifications()
    expect(observer).toHaveBeenCalledTimes(3)
    expect(store.getSnapshot(A)).toBe(editing)

    const changedItems = toggleItem(completed.layout.items, 'goals-progress', false)
    const expectedDraft = toPersistedLayout(A, changedItems, DASHBOARD_WIDGETS)
    store.setDraft(session, changedItems)
    const changed = store.getSnapshot(A)
    expect(changed).not.toBe(editing)
    expect(store.getSnapshot(A)).toBe(changed)
    expect(changed).toMatchObject({
      writable: true, state: 'editing', readPending: false, pending: false,
      layout: completed.layout, openedBaseline: baselineBefore, draft: expectedDraft,
      overlay: {}, counters: completed.counters,
    })
    expect(changed.overlay).toEqual({})
    expect(changed.draft).not.toEqual(draftBefore)
    expect(changed.draft?.items.filter((item) => item.visible)).toHaveLength(5)
    expect(observer).toHaveBeenCalledTimes(3)
    await drainNotifications()
    expect(observed).toHaveLength(4)
    expect(observed[3]).toBe(changed)
    expectDeepFrozen(changed)
    const changedBefore = structuredClone(changed)

    unsubscribe()
    unsubscribe()
    store.setDraft(session, toggleItem(expectedDraft.items, 'hero-balance', false))
    const afterUnsubscribe = store.getSnapshot(A)
    expect(afterUnsubscribe).not.toBe(changed)
    expect(store.getSnapshot(A)).toBe(afterUnsubscribe)
    expect(afterUnsubscribe.draft).toEqual(toPersistedLayout(
      A, toggleItem(expectedDraft.items, 'hero-balance', false), DASHBOARD_WIDGETS,
    ))
    expect(afterUnsubscribe.draft).not.toEqual(changed.draft)
    expect(afterUnsubscribe.draft?.items.filter((item) => item.visible)).toHaveLength(4)
    await drainNotifications()
    expect(observer).toHaveBeenCalledTimes(4)
    expect(observed).toHaveLength(4)
    expectDeepFrozen(afterUnsubscribe)
    expect(initial).toEqual(initialBefore)
    expect(pending).toEqual(pendingBefore)
    expect(completed).toEqual(completedBefore)
    expect(editing).toEqual(editingBefore)
    expect(editing.openedBaseline).toEqual(baselineBefore)
    expect(editing.draft).toEqual(draftBefore)
    expect(changed).toEqual(changedBefore)
    expect(store.getSnapshot(B).layout).toEqual(siblingBefore)
    expect(authSeed).toEqual(seedBefore)
    expect(authSeed.preferences).toBe(preferencesBefore)
    expect(authSeed.preferences.dashboard_layouts?.[B]).toEqual(siblingBefore)
    expect(entryUser).toEqual(entryBefore)
    expect(entryUser.preferences.dashboard_layouts?.[B]).toBe(sibling)
    expect(sibling).toEqual(siblingBefore)
    expect(DASHBOARD_WIDGETS).toEqual(registryBefore)
    expect([Object.isFrozen(DASHBOARD_WIDGETS), ...DASHBOARD_WIDGETS.map(Object.isFrozen)]).toEqual(registryFrozenBefore)
    expect(me).toHaveBeenCalledTimes(1)
    expect(saveLayout).not.toHaveBeenCalled()
    expect(updateMe).not.toHaveBeenCalled()
  } finally {
    // File-local boundary restoration, not product cleanup or completion proof.
    vi.doUnmock('@/lib/api')
    vi.restoreAllMocks()
    vi.resetModules()
  }
})
