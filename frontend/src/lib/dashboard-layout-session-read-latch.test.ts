import { expect, it, vi } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout,
  toPersistedLayout,
  toggleItem,
  type CommittedDashboardLayout,
  type DashboardLayout,
  type DashboardRequestBinding,
} from '@/lib/dashboard-layout'
import type { SessionStore, StoreOptions } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}

// Transport-fixture immutability only; no store or latch implementation.
function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}

it('releases a synchronously failed read latch, deduplicates same-owner reentrant recovery, and saves the retained changed draft', async () => {
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
  // Independent literal GET replies and PUT reply; none uses captured requests.
  const initialUser: User = freezeFixture({
    id: ACTOR, email: 'read-latch-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Synthetic read-latch tracer',
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
  const recoveryUser: User = freezeFixture({
    id: ACTOR, email: 'read-latch-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Synthetic read-latch tracer',
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
  const committed: CommittedDashboardLayout = freezeFixture({
    workspaceId: A,
    layout: {
      version: 1, workspaceId: A,
      items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  })
  const postUser: User = freezeFixture({
    id: ACTOR, email: 'read-latch-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Synthetic read-latch tracer',
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
            { id: 'goals-progress', visible: false, order: 4 },
            { id: 'period-transactions', visible: true, order: 5 },
          ],
        },
      },
    },
  })
  const authSeed: Readonly<User> = freezeFixture(structuredClone(initialUser))
  const seedBefore = structuredClone(authSeed)
  const preferencesBefore = authSeed.preferences
  const siblingBefore = structuredClone(sibling)
  const usersBefore = [initialUser, recoveryUser, postUser].map((user) => structuredClone(user))
  const committedBefore = structuredClone(committed)
  const entryBarrier = deferred<User>()
  const recoveryBarrier = deferred<User>()
  const putBarrier = deferred<CommittedDashboardLayout>()
  const putStarted = deferred<void>()
  const postBarrier = deferred<User>()
  type Phase = 'entry' | 'sync-failure' | 'recovery' | 'save'
  let phase: Phase = 'entry'
  let store!: SessionStore
  let injectReentry = true
  let boundaryReadPending: boolean | undefined
  let nestedRead: Promise<void> | undefined
  let nestedOutcome: Promise<{ ok: boolean; error?: unknown }> | undefined
  const captured: Array<{
    workspaceId: string
    layout: DashboardLayout
    binding: DashboardRequestBinding
  }> = []
  const me = vi.fn<StoreOptions['api']['me']>(() => {
    if (phase === 'entry') return entryBarrier.promise
    if (phase === 'sync-failure') {
      // Ordinary synchronous exception, not a rejected promise or fake TypeError.
      throw new Error('Synthetic synchronous GET boundary failure')
    }
    if (phase === 'recovery') {
      if (injectReentry) {
        injectReentry = false
        boundaryReadPending = store.getSnapshot(A).readPending
        nestedRead = store.refresh(A)
        nestedOutcome = nestedRead.then(
          () => ({ ok: true }), (error: unknown) => ({ ok: false, error }),
        )
      }
      // Never return nestedRead: transport settlement is an independent barrier.
      return recoveryBarrier.promise
    }
    return postBarrier.promise
  })
  const saveLayout = vi.fn<StoreOptions['api']['saveLayout']>((workspaceId, layout, binding) => {
    // Count every dispatch unconditionally; do not imitate the product write guard.
    captured.push({ workspaceId, layout: structuredClone(layout), binding })
    putStarted.resolve()
    return putBarrier.promise
  })
  const updateMe = vi.fn<(...args: unknown[]) => Promise<User>>(() => {
    return Promise.reject(new Error('Forbidden generic auth.updateMe'))
  })

  vi.resetModules()
  vi.doMock('@/lib/api', () => ({ auth: { me, updateMe }, dashboard: { saveLayout } }))
  try {
    const { createDashboardLayoutSession } = await import('@/lib/dashboard-layout-session')
    store = createDashboardLayoutSession({
      registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: 1, authSeed,
      getTokenIdentity: () => 'synthetic-read-latch-boundary-token',
      getWorkspaceId: () => A, api: { me, saveLayout },
    })
    const entryRead = store.refresh(A)
    const entryOutcome = entryRead.then(() => ({ ok: true }), (error: unknown) => ({ ok: false, error }))
    expect(me).toHaveBeenCalledTimes(1)
    expect(store.getSnapshot(A).readPending).toBe(true)
    entryBarrier.resolve(initialUser)
    expect(await entryOutcome).toEqual({ ok: true })
    await store.whenIdle()
    const defaults = resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, A)
    expect(store.getSnapshot(A)).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A,
      writable: true, readPending: false, provenance: 'saved', layout: defaults,
    })
    expect(store.getSnapshot(B).layout).toEqual(siblingBefore)
    const session = store.open(A)
    const changed = toPersistedLayout(A, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
    expect(changed).not.toEqual(defaults)
    expect(changed).toEqual(committed.layout)
    store.setDraft(session, changed.items)
    const opened = store.getSnapshot(A)
    expect(opened.openedBaseline).toEqual(defaults)
    expect(opened.draft).toEqual(changed)
    const baselineBefore = structuredClone(opened.openedBaseline)
    const draftBefore = structuredClone(opened.draft)
    const displayBefore = structuredClone(opened.layout)
    expect(saveLayout).toHaveBeenCalledTimes(0)
    expect(updateMe).toHaveBeenCalledTimes(0)

    phase = 'sync-failure'
    const failedRead = store.refresh(A)
    const failedOutcome = failedRead.then(
      () => ({ ok: true as const }),
      (error: unknown) => ({ ok: false as const, error }),
    )
    expect(me).toHaveBeenCalledTimes(2)
    const failure = await failedOutcome
    expect(failure.ok).toBe(false)
    if (failure.ok) throw new Error('Synchronous boundary failure unexpectedly refreshed')
    expect(failure.error).toBeInstanceOf(Error)
    expect(failure.error).not.toBeInstanceOf(TypeError)
    expect((failure.error as Error).message).toBe('Dashboard layout refresh did not establish a current writable target')
    await store.whenIdle()
    expect(me).toHaveBeenCalledTimes(2)
    expect(saveLayout).toHaveBeenCalledTimes(0)
    expect(updateMe).toHaveBeenCalledTimes(0)
    expect(store.getSnapshot(A)).toMatchObject({
      writable: false, state: 'editing', layout: displayBefore,
      openedBaseline: baselineBefore, draft: draftBefore,
    })
    expect(store.getSnapshot(B).layout).toEqual(siblingBefore)
    expect(authSeed).toEqual(seedBefore)
    expect(authSeed.preferences).toBe(preferencesBefore)
    expect([initialUser, recoveryUser, postUser]).toEqual(usersBefore)
    // Expected first genuine RED: finally clears read before refresh re-latches it.
    expect(store.getSnapshot(A).readPending).toBe(false)

    // Initially NOT_REACHED on the frozen source; same store/owner/session recovery.
    phase = 'recovery'
    const recoveryRead = store.refresh(A)
    const recoveryOutcome = recoveryRead.then(() => ({ ok: true }), (error: unknown) => ({ ok: false, error }))
    expect(me).toHaveBeenCalledTimes(3)
    expect(injectReentry).toBe(false)
    expect(boundaryReadPending).toBe(true)
    expect(store.getSnapshot(A).readPending).toBe(true)
    expect(nestedRead).toBe(recoveryRead)
    expect(nestedOutcome).toBeDefined()
    recoveryBarrier.resolve(recoveryUser)
    await Promise.all([recoveryRead, nestedRead])
    expect(await recoveryOutcome).toEqual({ ok: true })
    expect(await nestedOutcome).toEqual({ ok: true })
    await store.whenIdle()
    expect(me).toHaveBeenCalledTimes(3)
    expect(store.getSnapshot(A)).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A,
      writable: true, readPending: false, state: 'editing', layout: displayBefore,
      openedBaseline: baselineBefore, draft: draftBefore,
    })
    expect(store.getSnapshot(B).layout).toEqual(siblingBefore)
    expect(authSeed).toEqual(seedBefore)
    expect(authSeed.preferences).toBe(preferencesBefore)
    expect(saveLayout).toHaveBeenCalledTimes(0)
    expect(updateMe).toHaveBeenCalledTimes(0)

    phase = 'save'
    const completion = store.finalize(session)
    const completionOutcome = completion.then(() => ({ ok: true }), (error: unknown) => ({ ok: false, error }))
    expect(store.getSnapshot(A).pending).toBe(true)
    await putStarted.promise
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(captured).toHaveLength(1)
    expect(captured[0].workspaceId).toBe(A)
    expect(captured[0].layout).toEqual(draftBefore)
    expect(captured[0].binding.isCurrent()).toBe(true)
    expect(Object.keys(captured[0].layout).sort()).toEqual(['items', 'version', 'workspaceId'])
    captured[0].layout.items.forEach((item, order) => {
      expect(Object.keys(item).sort()).toEqual(['id', 'order', 'visible'])
      expect(item.order).toBe(order)
    })
    putBarrier.resolve(committed)
    postBarrier.resolve(postUser)
    expect(await completionOutcome).toEqual({ ok: true })
    await store.whenIdle()
    expect(me).toHaveBeenCalledTimes(4)
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(updateMe).toHaveBeenCalledTimes(0)
    expect(store.getSnapshot(A)).toMatchObject({
      writable: true, state: 'closed-success', pending: false, readPending: false,
      provenance: 'saved', layout: committed.layout, openedBaseline: null, draft: null,
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(store.getSnapshot(A).overlay).toEqual({ [A]: committed.layout })
    expect(store.getSnapshot(B).layout).toEqual(siblingBefore)
    expect(sibling).toEqual(siblingBefore)
    expect(authSeed).toEqual(seedBefore)
    expect(authSeed.preferences).toBe(preferencesBefore)
    expect(authSeed.preferences.dashboard_layouts?.[B]).toEqual(siblingBefore)
    expect(initialUser.preferences.dashboard_layouts?.[B]).toBe(sibling)
    expect(recoveryUser.preferences.dashboard_layouts?.[B]).toBe(sibling)
    expect(postUser.preferences.dashboard_layouts?.[B]).toBe(sibling)
    expect([initialUser, recoveryUser, postUser]).toEqual(usersBefore)
    expect(committed).toEqual(committedBefore)
  } finally {
    // Restore boundary spies only; do not touch product latches or owner lifetime.
    vi.doUnmock('@/lib/api')
    vi.restoreAllMocks()
    vi.resetModules()
  }
}, 10_000)
