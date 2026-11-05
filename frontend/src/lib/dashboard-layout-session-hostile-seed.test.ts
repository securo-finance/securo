import { expect, it, vi } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resetLayout, toPersistedLayout, toggleItem,
  type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding,
} from '@/lib/dashboard-layout'
import type { SessionStore, StoreOptions } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

const W = '11111111-1111-4111-8111-111111111111'
const ACTOR = '33333333-3333-4333-8333-333333333333'

it('initializes safely from hostile Auth seed preferences then saves one changed layout on the same owner', async () => {
  const defaults = resetLayout(DASHBOARD_WIDGETS, W)
  const authSeed: User = {
    id: ACTOR, email: 'hostile-seed@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {},
  }
  const unreadable = new Error('H4 distinct hostile Auth seed preferences read')
  let getterReads = 0
  Object.defineProperty(authSeed, 'preferences', {
    get() { getterReads += 1; throw unreadable },
    enumerable: true, configurable: false,
  })
  Object.freeze(authSeed)
  const seedDescriptor = Object.getOwnPropertyDescriptor(authSeed, 'preferences')

  // Independent literal READ: same actor/workspace, with nonlayout siblings.
  const entryUser: User & { preferences: { timezone: string } } = {
    id: ACTOR, email: 'hostile-seed@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic hostile seed tracer',
      dashboard_layouts: { [W]: { version: 1, workspaceId: W, items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: true, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ] } },
    },
  }
  // Independent literal accepted envelope, never generated from a mock call.
  const reply: CommittedDashboardLayout = {
    workspaceId: W, layout: { version: 1, workspaceId: W, items: [
      { id: 'hero-balance', visible: false, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ] },
  }
  // Independent literal post-PUT READ, including the unchanged siblings.
  const postUser: User & { preferences: { timezone: string } } = {
    id: ACTOR, email: 'hostile-seed@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic hostile seed tracer',
      dashboard_layouts: { [W]: { version: 1, workspaceId: W, items: [
        { id: 'hero-balance', visible: false, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: true, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ] } },
    },
  }
  const entryBefore = structuredClone(entryUser)
  const replyBefore = structuredClone(reply)
  const postBefore = structuredClone(postUser)
  const captured: Array<{
    workspaceId: string; layout: DashboardLayout; binding: DashboardRequestBinding
    currentAtDispatch: boolean
  }> = []
  // Source-derived budget: refresh issues GET; open issues none; finalize adds post-GET.
  const me = vi.fn<StoreOptions['api']['me']>(() => {
    throw new Error('Unexpected GET beyond explicit refresh and post-PUT READ')
  }).mockResolvedValueOnce(entryUser).mockResolvedValueOnce(postUser)
  const saveLayout = vi.fn<StoreOptions['api']['saveLayout']>((workspaceId, layout, binding) => {
    captured.push({ workspaceId, layout: structuredClone(layout), binding, currentAtDispatch: binding.isCurrent() })
    if (captured.length !== 1) throw new Error('Unexpected additional PUT')
    return Promise.resolve(reply)
  })
  const updateMe = vi.fn<(...args: unknown[]) => Promise<User>>(() => {
    throw new Error('Forbidden legacy Auth preferences PATCH')
  })
  const updateUser = vi.fn<(user: User) => void>(() => {
    throw new Error('Forbidden legacy Auth user replacement')
  })
  // Import-boundary sentinels only: no mounted Auth/provider or hook proof.
  vi.resetModules()
  vi.doMock('@/lib/api', () => ({ auth: { me, updateMe }, dashboard: { saveLayout } }))
  vi.doMock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: authSeed, updateUser }) }))
  let store: SessionStore | undefined
  const issued: Array<Promise<{ ok: true } | { ok: false; error: unknown }>> = []
  function observe(task: Promise<void>) {
    const outcome = task.then(
      () => ({ ok: true as const }),
      (error: unknown) => ({ ok: false as const, error }),
    )
    issued.push(outcome)
    return outcome
  }
  let bodyFailed = false
  const cleanupErrors: unknown[] = []
  try {
    const { createDashboardLayoutSession: actualFactory } = await import('@/lib/dashboard-layout-session')
    expect(() => {
      store = actualFactory({
        registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: 1, authSeed,
        getTokenIdentity: () => 'synthetic-hostile-seed-owner-identity',
        getWorkspaceId: () => W, api: { me, saveLayout },
      })
    }, 'BEHAVIOR H4: unreadable Auth seed preferences must not abort owner initialization').not.toThrow()
    // Failed construction exits at the assertion above; never fabricate an owner.
    if (!store) throw new Error('Actual factory did not return an owner')
    expect(getterReads).toBe(1)
    const initial = store.getSnapshot(W)
    expect(initial.layout).toEqual(defaults)
    expect(initial).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: W,
      state: 'closed', writable: false, pending: false, readPending: false, canUndo: false,
      openedBaseline: null, draft: null,
    })
    expect(initial.overlay).toEqual({})
    expect(initial.counters).toEqual({ success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
    expect(me).toHaveBeenCalledTimes(0)
    expect(saveLayout).toHaveBeenCalledTimes(0)
    expect(updateMe).toHaveBeenCalledTimes(0)
    expect(updateUser).toHaveBeenCalledTimes(0)

    expect(await observe(store.refresh(W))).toEqual({ ok: true })
    const baseline = entryBefore.preferences.dashboard_layouts?.[W]
    expect(baseline).toEqual(defaults)
    const refreshed = store.getSnapshot(W)
    expect(refreshed.layout).toEqual(baseline)
    expect(refreshed.writable).toBe(true)
    expect(me).toHaveBeenCalledTimes(1)
    const id = store.open(W)
    const editing = store.getSnapshot(W)
    expect(editing.state).toBe('editing')
    expect(editing.openedBaseline).toEqual(baseline)
    expect(editing.draft).toEqual(baseline)
    expect(editing.overlay).toEqual({})
    const expected = toPersistedLayout(W, toggleItem(defaults.items, 'hero-balance', false), DASHBOARD_WIDGETS)
    expect(expected).not.toEqual(baseline)
    expect(expected).toEqual(replyBefore.layout)
    expect(expected.items.filter((item, index) => item.visible !== defaults.items[index].visible))
      .toEqual([{ id: 'hero-balance', visible: false, order: 0 }])
    store.setDraft(id, expected.items)
    expect(store.getSnapshot(W).draft).toEqual(expected)
    expect(store.getSnapshot(W).openedBaseline).toEqual(baseline)
    expect(saveLayout).toHaveBeenCalledTimes(0)
    expect(await observe(store.finalize(id))).toEqual({ ok: true })
    await store.whenIdle()
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(captured).toHaveLength(1)
    const request = captured[0]
    if (!request) throw new Error('Scoped PUT request was not captured')
    expect(request.workspaceId).toBe(W)
    expect(request.layout).toEqual(expected)
    expect(Reflect.ownKeys(request.layout).sort()).toEqual(['items', 'version', 'workspaceId'])
    request.layout.items.forEach((item) => {
      expect(Reflect.ownKeys(item).sort()).toEqual(['id', 'order', 'visible'])
    })
    expect(request.currentAtDispatch).toBe(true)
    expect(request.binding.isCurrent()).toBe(false)
    expect(me).toHaveBeenCalledTimes(2)
    expect(me.mock.calls).toEqual([[], []])
    const saved = store.getSnapshot(W)
    expect(saved).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: W,
      state: 'closed-success', provenance: 'saved', writable: true,
      pending: false, readPending: false, canUndo: false, openedBaseline: null, draft: null,
    })
    expect(saved.layout).toEqual(expected)
    expect(saved.overlay).toEqual({ [W]: expected })
    expect(saved.counters).toEqual({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
    expect(updateMe).toHaveBeenCalledTimes(0)
    expect(updateUser).toHaveBeenCalledTimes(0)
    expect(authSeed.id).toBe(ACTOR)
    expect(Object.getOwnPropertyDescriptor(authSeed, 'preferences')).toEqual(seedDescriptor)
    expect(getterReads).toBe(1)
    expect(entryUser).toEqual(entryBefore)
    expect(reply).toEqual(replyBefore)
    expect(postUser).toEqual(postBefore)
  } catch (error: unknown) {
    bodyFailed = true
    throw error // Preserve the original behavioral assertion, including construction RED.
  } finally {
    try {
      const outcomes = await Promise.all(issued)
      outcomes.forEach((outcome) => { if (!outcome.ok) cleanupErrors.push(outcome.error) })
      if (store) await store.whenIdle()
    } catch (error: unknown) { cleanupErrors.push(error) }
    try {
      if (store) store.revoke()
      await new Promise<void>((resolve) => { queueMicrotask(resolve) })
    } catch (error: unknown) { cleanupErrors.push(error) }
    vi.doUnmock('@/lib/api')
    vi.doUnmock('@/contexts/auth-context')
    vi.resetModules()
    if (bodyFailed && cleanupErrors.length > 0) {
      console.error('Hostile seed cleanup diagnostics; primary assertion preserved', cleanupErrors)
    }
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'Hostile seed cleanup failed')
})
