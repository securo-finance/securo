import { expect, it, vi } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout,
  toPersistedLayout,
  toggleItem,
  validateCommittedDashboardLayout,
  validateDashboardLayoutRequest,
  type CommittedDashboardLayout,
  type DashboardLayout,
  type DashboardRequestBinding,
} from '@/lib/dashboard-layout'
import { createDashboardLayoutSession, type SessionStore, type StoreOptions } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// Proposed ports only: no implementation, adapter, or product-module mock.
interface ProposedPorts {
  retain(workspaceId: string): string | null
  discard(sessionId: string): void
}

const A = '11111111-1111-4111-8111-111111111111'
const ACTOR = '33333333-3333-4333-8333-333333333333'

function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Resolver not initialized') }
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}

// A finite notification barrier, not polling or transport settlement.
function drainNotifications(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('retains the editing ID across a held read, discards only its unsent draft, and lets the same owner save a fresh session', async () => {
  const initialUser: User = {
    id: ACTOR, email: 'page-session-ports@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: { display_name: 'Synthetic page-session ports tracer' },
  }
  // Each PUT reply and matching GET is independently literal, never request-derived.
  const firstReply: CommittedDashboardLayout = {
    workspaceId: A,
    layout: { version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ] },
  }
  const firstPostUser: User = {
    id: ACTOR, email: 'page-session-ports@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: { display_name: 'Synthetic page-session ports tracer', dashboard_layouts: {
      [A]: { version: 1, workspaceId: A, items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ] },
    } },
  }
  const secondReply: CommittedDashboardLayout = {
    workspaceId: A,
    layout: { version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ] },
  }
  const secondPostUser: User = {
    id: ACTOR, email: 'page-session-ports@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: { display_name: 'Synthetic page-session ports tracer', dashboard_layouts: {
      [A]: { version: 1, workspaceId: A, items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: false, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ] },
    } },
  }
  const authSeed: Readonly<User> = structuredClone(initialUser)
  const seedBefore = structuredClone(authSeed)
  const held = deferred<User>()
  const captured: Array<{
    workspaceId: string
    layout: DashboardLayout
    binding: DashboardRequestBinding
    currentAtDispatch: boolean
  }> = []
  const me = vi.fn<StoreOptions['api']['me']>(() => {
    throw new Error('Unexpected GET outside the finite lifecycle')
  })
    .mockResolvedValueOnce(initialUser)
    .mockResolvedValueOnce(firstPostUser)
    .mockImplementationOnce(() => held.promise)
    .mockResolvedValueOnce(secondPostUser)
  const saveLayout = vi.fn<StoreOptions['api']['saveLayout']>((workspaceId, layout, binding) => {
    captured.push({ workspaceId, layout: structuredClone(layout), binding, currentAtDispatch: binding.isCurrent() })
    if (captured.length === 1) return Promise.resolve(firstReply)
    if (captured.length === 2) return Promise.resolve(secondReply)
    throw new Error('Unexpected PUT outside the two explicit changed saves')
  })
  const store: SessionStore & Partial<ProposedPorts> = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: 1, authSeed,
    getTokenIdentity: () => 'synthetic-page-session-ports-identity',
    getWorkspaceId: () => A, api: { me, saveLayout },
  })
  const observer = vi.fn(() => { store.getSnapshot(A) })
  const unsubscribe = store.subscribe(observer)
  const issued: Array<Promise<{ ok: boolean; error?: unknown }>> = []
  // Observe rejection immediately, including when a later assertion fails.
  function observe(task: Promise<void>) {
    const outcome = task.then(() => ({ ok: true }), (error: unknown) => ({ ok: false, error }))
    issued.push(outcome)
    return outcome
  }
  const oneSuccess = { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 }
  try {
    expect(validateCommittedDashboardLayout(A, firstReply, DASHBOARD_WIDGETS)).toEqual(firstReply)
    expect(validateCommittedDashboardLayout(A, secondReply, DASHBOARD_WIDGETS)).toEqual(secondReply)
    expect(firstPostUser.preferences.dashboard_layouts?.[A]).toEqual(firstReply.layout)
    expect(secondPostUser.preferences.dashboard_layouts?.[A]).toEqual(secondReply.layout)
    expect(await observe(store.refresh(A))).toEqual({ ok: true })
    const defaults = resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, A)
    expect(store.getSnapshot(A)).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A, writable: true,
      state: 'closed', readPending: false, layout: defaults, overlay: {},
      counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    const savedId = store.open(A)
    const firstChanged = toPersistedLayout(A, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
    expect(firstChanged).not.toEqual(defaults)
    expect(validateDashboardLayoutRequest(A, firstChanged, DASHBOARD_WIDGETS)).toEqual(firstReply.layout)
    store.setDraft(savedId, firstChanged.items)
    expect(await observe(store.finalize(savedId))).toEqual({ ok: true })
    expect(me).toHaveBeenCalledTimes(2)
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(captured).toHaveLength(1)
    expect(captured[0]).toMatchObject({ workspaceId: A, layout: firstReply.layout, currentAtDispatch: true })
    expect(typeof captured[0].binding.isCurrent).toBe('function')
    const saved = store.getSnapshot(A)
    expect(saved).toMatchObject({
      writable: true, state: 'closed-success', readPending: false,
      layout: firstReply.layout, overlay: { [A]: firstReply.layout }, counters: oneSuccess,
    })
    const savedOverlay = structuredClone(saved.overlay)
    const editingId = store.open(A)
    expect(editingId).not.toBe(savedId)
    const unsent = toPersistedLayout(A, toggleItem(firstChanged.items, 'hero-balance', false), DASHBOARD_WIDGETS)
    expect(unsent).not.toEqual(firstReply.layout)
    store.setDraft(editingId, unsent.items)
    await drainNotifications()
    const editing = store.getSnapshot(A)
    expect(editing).toMatchObject({
      state: 'editing', openedBaseline: firstReply.layout, draft: unsent,
      overlay: savedOverlay, counters: oneSuccess,
    })

    // The first intended RED is a collected behavior-capability assertion.
    expect(typeof store.retain, 'BEHAVIOR capability: retain must query the current editing ID without opening or publishing').toBe('function')
    if (typeof store.retain !== 'function') throw new Error('retain capability assertion did not narrow')
    const editingPublications = observer.mock.calls.length
    expect(store.retain(A)).toBe(editingId)
    expect(store.retain(A)).toBe(editingId)
    expect(store.getSnapshot(A)).toBe(editing)
    expect(store.getSnapshot(A).counters).toEqual(oneSuccess)
    await drainNotifications()
    expect(observer).toHaveBeenCalledTimes(editingPublications)

    const readOutcome = observe(store.refresh(A))
    expect(me).toHaveBeenCalledTimes(3)
    const reading = store.getSnapshot(A)
    expect(reading).toMatchObject({
      state: 'editing', readPending: true, openedBaseline: firstReply.layout,
      draft: unsent, overlay: savedOverlay, counters: oneSuccess,
    })
    await drainNotifications()
    const readingPublications = observer.mock.calls.length
    expect(store.retain(A), 'held authoritative read must not lose or replace the editing ID').toBe(editingId)
    expect(store.retain(A)).toBe(editingId)
    expect(store.getSnapshot(A)).toBe(reading)
    await drainNotifications()
    expect(observer).toHaveBeenCalledTimes(readingPublications)
    held.resolve(firstPostUser)
    expect(await readOutcome).toEqual({ ok: true })
    await drainNotifications()
    const settled = store.getSnapshot(A)
    expect(settled).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A, writable: true,
      state: 'editing', readPending: false, layout: firstReply.layout,
      openedBaseline: firstReply.layout, draft: unsent, overlay: savedOverlay, counters: oneSuccess,
    })
    const settledPublications = observer.mock.calls.length
    expect(store.retain(A)).toBe(editingId)
    expect(store.getSnapshot(A)).toBe(settled)
    await drainNotifications()
    expect(observer).toHaveBeenCalledTimes(settledPublications)
    expect(saveLayout).toHaveBeenCalledTimes(1)

    expect(typeof store.discard, 'BEHAVIOR capability: discard must close only the matching unsent edit and preserve its owner and saved overlay').toBe('function')
    if (typeof store.discard !== 'function') throw new Error('discard capability assertion did not narrow')
    store.discard(editingId)
    await drainNotifications()
    const discarded = store.getSnapshot(A)
    expect(discarded).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A, writable: true,
      state: 'closed', pending: false, readPending: false,
      openedBaseline: null, draft: null, layout: firstReply.layout,
      overlay: savedOverlay, counters: oneSuccess,
    })
    expect(store.retain(A)).toBeNull()
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(me).toHaveBeenCalledTimes(3)
    expect(captured).toHaveLength(1)

    // The same real store/owner explicitly reopens; discard is not global revoke.
    const freshId = store.open(A)
    expect(freshId).not.toBe(editingId)
    expect(freshId).not.toBe(savedId)
    const secondChanged = toPersistedLayout(A, toggleItem(firstChanged.items, 'spending-charts', false), DASHBOARD_WIDGETS)
    expect(secondChanged).not.toEqual(firstReply.layout)
    expect(secondChanged).not.toEqual(unsent)
    expect(validateDashboardLayoutRequest(A, secondChanged, DASHBOARD_WIDGETS)).toEqual(secondReply.layout)
    store.setDraft(freshId, secondChanged.items)
    await drainNotifications()
    const fresh = store.getSnapshot(A)
    const freshPublications = observer.mock.calls.length
    store.discard(editingId)
    expect(store.retain(A), 'stale discard must not remove the explicitly fresh session').toBe(freshId)
    expect(store.getSnapshot(A)).toBe(fresh)
    expect(fresh).toMatchObject({
      state: 'editing', openedBaseline: firstReply.layout, draft: secondReply.layout,
      overlay: savedOverlay, counters: oneSuccess, writable: true,
    })
    await drainNotifications()
    expect(observer).toHaveBeenCalledTimes(freshPublications)
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(await observe(store.finalize(freshId))).toEqual({ ok: true })
    expect(saveLayout).toHaveBeenCalledTimes(2)
    expect(me).toHaveBeenCalledTimes(4)
    expect(captured).toHaveLength(2)
    expect(captured[1]).toMatchObject({ workspaceId: A, layout: secondReply.layout, currentAtDispatch: true })
    expect(typeof captured[1].binding.isCurrent).toBe('function')
    expect(store.getSnapshot(A)).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A, writable: true,
      state: 'closed-success', pending: false, readPending: false,
      openedBaseline: null, draft: null, layout: secondReply.layout,
      overlay: { [A]: secondReply.layout },
      counters: { success: 2, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(authSeed).toEqual(seedBefore)
  } finally {
    // Release the only held transport even if an earlier assertion throws.
    held.resolve(firstPostUser)
    await Promise.allSettled(issued)
    await store.whenIdle()
    unsubscribe()
    store.revoke()
    await drainNotifications()
  }
})
