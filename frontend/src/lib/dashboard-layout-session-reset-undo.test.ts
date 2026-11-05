import { expect, it, vi } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resetLayout, toPersistedLayout, toggleItem,
  validateCommittedDashboardLayout, validateDashboardLayoutRequest,
  type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding,
} from '@/lib/dashboard-layout'
import {
  createDashboardLayoutSession,
  type SessionSnapshot, type SessionStore, type StoreOptions,
} from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// Prospective capabilities only; the factory and every snapshot remain real.
interface ProposedResetUndoPorts {
  reset(sessionId: string): void
  undoReset(sessionId: string): void
}

const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'
const editableError = 'Dashboard layout session is not editable'

function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Resolver not initialized') }
  let reject: (reason: unknown) => void = () => { throw new Error('Rejecter not initialized') }
  const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse })
  // Transport rejection handling exists before any possible stimulus.
  void promise.catch(() => {})
  return { promise, resolve, reject }
}

function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}

// A finite native notification checkpoint, never a held-transport idle wait.
function notifications(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('owns one-deep reset undo across preview, unchanged close, changed save, held read, discard and revoke', async () => {
  // Initialize every controlled GET/PUT (both settlement paths) before premises.
  const entryGet = deferred<User>()
  const postGet = deferred<User>()
  const heldGet = deferred<User>()
  const put = deferred<CommittedDashboardLayout>()
  const sibling: DashboardLayout = freezeFixture({ version: 1, workspaceId: B, items: [
    { id: 'hero-balance', visible: false, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const entryUser: User = freezeFixture({
    id: ACTOR, email: 'reset-undo@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic reset undo tracer',
      dashboard_layouts: { [B]: sibling, [A]: { version: 1, workspaceId: A, items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ] } },
    },
  })
  // Independent literal accepted envelope; not an echo of the submitted request.
  const reply: CommittedDashboardLayout = freezeFixture({
    workspaceId: A, layout: { version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: false, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ] },
  })
  // Independently literal matching authoritative post-PUT user.
  const postUser: User = freezeFixture({
    id: ACTOR, email: 'reset-undo@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic reset undo tracer',
      dashboard_layouts: {
        [B]: { version: 1, workspaceId: B, items: [
          { id: 'hero-balance', visible: false, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
        [A]: { version: 1, workspaceId: A, items: [
          { id: 'hero-balance', visible: false, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: false, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
      },
    },
  })
  const authSeed: Readonly<User> = freezeFixture(structuredClone(entryUser))
  const seedBefore = structuredClone(authSeed)
  const entryBefore = structuredClone(entryUser)
  const postBefore = structuredClone(postUser)
  const replyBefore = structuredClone(reply)
  const registryBefore = structuredClone(DASHBOARD_WIDGETS)
  const captured: Array<{
    workspaceId: string; layout: DashboardLayout; binding: DashboardRequestBinding
    currentAtDispatch: boolean
  }> = []
  const me = vi.fn<StoreOptions['api']['me']>(() => {
    throw new Error('Unexpected GET beyond the three controlled reads')
  }).mockImplementationOnce(() => entryGet.promise)
    .mockImplementationOnce(() => postGet.promise)
    .mockImplementationOnce(() => heldGet.promise)
  const saveLayout = vi.fn<StoreOptions['api']['saveLayout']>((workspaceId, layout, binding) => {
    captured.push({ workspaceId, layout: structuredClone(layout), binding, currentAtDispatch: binding.isCurrent() })
    if (captured.length !== 1) throw new Error('Unexpected additional PUT')
    return put.promise
  })
  // Boundary sentinel only: no Auth/provider replacement or mounted writer claim.
  const updateMe = vi.fn<(...args: unknown[]) => Promise<User>>(() => {
    throw new Error('Forbidden generic preferences writer')
  })
  const api = { me, saveLayout, updateMe }
  const store: SessionStore & Partial<ProposedResetUndoPorts> = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: 1, authSeed,
    getTokenIdentity: () => 'synthetic-reset-undo-owner-identity',
    getWorkspaceId: () => A, api,
  })
  function snapshot(workspaceId = A): SessionSnapshot & { readonly canUndo?: boolean } {
    return store.getSnapshot(workspaceId)
  }
  const observed: SessionSnapshot[] = []
  const observerErrors: unknown[] = []
  const unsubscribe = store.subscribe(() => {
    // Witness only; assertions below are outside production callback isolation.
    try { observed.push(snapshot()) } catch (error: unknown) { observerErrors.push(error) }
  })
  const issued: Array<Promise<{ ok: boolean; error?: unknown }>> = []
  function observe(task: Promise<void>) {
    const outcome = task.then(() => ({ ok: true }), (error: unknown) => ({ ok: false, error }))
    issued.push(outcome)
    return outcome
  }
  const zero = { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 }
  const suppressed = { ...zero, suppressed: 1 }
  const succeeded = { ...suppressed, success: 1 }
  let phase = 'authoritative entry read'
  let primary: { phase: string; cause: unknown } | undefined
  const cleanupErrors: unknown[] = []
  try {
    const entryOutcome = observe(store.refresh(A))
    entryGet.resolve(entryUser)
    expect(await entryOutcome).toEqual({ ok: true })
    await store.whenIdle()
    await notifications()
    // FIRST post-read assertions: missing ports are observable BEHAVIOR RED.
    phase = 'BEHAVIOR reset/undo capabilities'
    expect(typeof store.reset, 'BEHAVIOR capability: owner reset must capture the current draft').toBe('function')
    expect(typeof store.undoReset, 'BEHAVIOR capability: owner undoReset must consume one captured draft').toBe('function')
    if (typeof store.reset !== 'function' || typeof store.undoReset !== 'function') {
      throw new Error('Capability assertions did not establish defined functions')
    }
    const reset = store.reset
    const undoReset = store.undoReset
    expect(snapshot().canUndo).toBe(false)

    phase = 'A exact preview reset and consuming undo'
    const defaults = resetLayout(DASHBOARD_WIDGETS, A)
    const savedBaseline = entryUser.preferences.dashboard_layouts?.[A]
    if (!savedBaseline) throw new Error('Literal saved baseline premise is missing')
    const S0 = toPersistedLayout(A, savedBaseline.items, DASHBOARD_WIDGETS)
    const D1 = toPersistedLayout(A, toggleItem(S0.items, 'hero-balance', false), DASHBOARD_WIDGETS)
    expect(validateDashboardLayoutRequest(A, S0, DASHBOARD_WIDGETS)).toEqual(S0)
    expect(validateDashboardLayoutRequest(A, D1, DASHBOARD_WIDGETS)).toEqual(reply.layout)
    expect(validateCommittedDashboardLayout(A, reply, DASHBOARD_WIDGETS)).toEqual(reply)
    expect(postUser.preferences.dashboard_layouts?.[A]).toEqual(reply.layout)
    expect(D1).not.toEqual(S0)
    expect(D1).not.toEqual(defaults)
    expect(S0).not.toEqual(defaults)
    expect(snapshot()).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A, writable: true,
      state: 'closed', provenance: 'saved', layout: S0, openedBaseline: null, draft: null,
    })
    expect(snapshot().overlay).toEqual({})
    expect(snapshot().counters).toEqual(zero)
    expect(snapshot(B).layout).toEqual(sibling)
    const id = store.open(A)
    store.setDraft(id, D1.items)
    await notifications()
    const editing = snapshot()
    const editingBefore = structuredClone(editing)
    const invariant = () => {
      const current = snapshot()
      expect(store.retain(A)).toBe(id)
      expect(current).toMatchObject({
        actorId: ACTOR, authGeneration: 1, workspaceId: A,
        state: 'editing', writable: true, pending: false, readPending: false,
      })
      expect(current.openedBaseline).toEqual(S0)
      expect(current.layout).toEqual(S0)
      expect(current.overlay).toEqual({})
      expect(current.counters).toEqual(zero)
      expect(me).toHaveBeenCalledTimes(1)
      expect(saveLayout).not.toHaveBeenCalled()
    }
    reset(id)
    invariant()
    expect(snapshot().draft).toEqual(defaults)
    expect(snapshot().canUndo).toBe(true)
    expect(snapshot()).toBe(snapshot())
    await notifications()
    expect(observed.at(-1)).toBe(snapshot())
    undoReset(id)
    invariant()
    expect(snapshot().draft).toEqual(D1)
    expect(snapshot().canUndo).toBe(false)
    await notifications()
    const undone = snapshot()
    const undoPublications = observed.length
    undoReset(id)
    expect(snapshot()).toBe(undone)
    await notifications()
    expect(observed).toHaveLength(undoPublications)
    expect(editing).toEqual(editingBefore)

    phase = 'B repeated reset replacement and boolean-only publication'
    reset(id)
    await notifications()
    const firstReset = snapshot()
    const beforeRepeat = observed.length
    reset(id) // Replace D1 with defaults in the one-deep slot, no public change.
    expect(snapshot()).toBe(firstReset)
    await notifications()
    expect(observed).toHaveLength(beforeRepeat)
    undoReset(id)
    const boolOnly = snapshot()
    expect(boolOnly).not.toBe(firstReset)
    expect(boolOnly).toEqual({ ...firstReset, canUndo: false })
    expect(boolOnly.draft).toEqual(defaults)
    expect(boolOnly.draft).not.toEqual(D1)
    expect(snapshot()).toBe(boolOnly)
    expect(observed).toHaveLength(beforeRepeat)
    await notifications()
    expect(observed).toHaveLength(beforeRepeat + 1)
    expect(observed.at(-1)).toBe(boolOnly)
    const beforeBoolReset = observed.length
    reset(id) // Defaults -> defaults, but false -> true must publish.
    const boolReset = snapshot()
    expect(boolReset).not.toBe(boolOnly)
    expect(boolReset).toEqual({ ...boolOnly, canUndo: true })
    await notifications()
    expect(observed).toHaveLength(beforeBoolReset + 1)
    expect(observed.at(-1)).toBe(boolReset)
    undoReset(id)
    await notifications()
    const consumedDefaults = snapshot()
    const beforeNoop = observed.length
    undoReset(id)
    expect(snapshot()).toBe(consumedDefaults)
    await notifications()
    expect(observed).toHaveLength(beforeNoop)
    invariant()
    store.setDraft(id, S0.items)
    reset(id)
    expect(snapshot().canUndo).toBe(true)
    undoReset(id)
    expect(snapshot().draft).toEqual(S0)
    expect(snapshot().canUndo).toBe(false)
    expect(await observe(store.finalize(id))).toEqual({ ok: true })
    await store.whenIdle()
    await notifications()
    const closed = snapshot()
    expect(closed).toMatchObject({ state: 'closed', openedBaseline: null, draft: null, layout: S0 })
    expect(closed.canUndo).toBe(false)
    expect(closed.counters).toEqual(suppressed)
    expect(closed.overlay).toEqual({})
    expect(saveLayout).not.toHaveBeenCalled()
    expect(me).toHaveBeenCalledTimes(1)
    expect(snapshot(B).layout).toEqual(sibling)
    expect(authSeed).toEqual(seedBefore)

    phase = 'C same-owner changed positive with held PUT and post-GET'
    const positiveId = store.open(A)
    expect(positiveId).not.toBe(id)
    store.setDraft(positiveId, D1.items)
    reset(positiveId)
    store.setDraft(positiveId, D1.items) // An ordinary edit must not silently erase undo.
    expect(snapshot().canUndo).toBe(true)
    const beforeElection = snapshot()
    const positiveOutcome = observe(store.finalize(positiveId))
    const pending = snapshot()
    expect(pending).toMatchObject({ state: 'pending', pending: true, readPending: false })
    expect(pending.canUndo).toBe(false)
    expect(pending.draft).toEqual(D1)
    expect(pending.openedBaseline).toEqual(S0)
    expect(pending.counters).toEqual(suppressed)
    expect(pending.overlay).toEqual({})
    expect(beforeElection.canUndo).toBe(true)
    expect(() => reset(positiveId)).toThrowError(new Error(editableError))
    expect(() => undoReset(positiveId)).toThrowError(new Error(editableError))
    expect(snapshot()).toBe(pending)
    await notifications() // Dispatch actual PUT, but never wait for held work to idle.
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(me).toHaveBeenCalledTimes(1)
    expect(captured).toHaveLength(1)
    const request = captured[0]
    if (!request) throw new Error('One captured PUT was not available')
    expect(request.workspaceId).toBe(A)
    expect(request.layout).toEqual(D1)
    expect(validateDashboardLayoutRequest(A, request.layout, DASHBOARD_WIDGETS)).toEqual(D1)
    expect(Reflect.ownKeys(request.layout).sort()).toEqual(['items', 'version', 'workspaceId'])
    request.layout.items.forEach((item) => {
      expect(Reflect.ownKeys(item).sort()).toEqual(['id', 'order', 'visible'])
    })
    expect(request.currentAtDispatch).toBe(true)
    expect(request.binding.isCurrent()).toBe(true)
    expect(snapshot()).toBe(pending)
    expect(() => reset(positiveId)).toThrowError(new Error(editableError))
    expect(() => undoReset(positiveId)).toThrowError(new Error(editableError))
    expect(snapshot()).toBe(pending)
    put.resolve(reply)
    await notifications()
    expect(me).toHaveBeenCalledTimes(2)
    expect(snapshot().readPending).toBe(true)
    expect(snapshot().canUndo).toBe(false)
    expect(snapshot().overlay).toEqual({ [A]: reply.layout })
    postGet.resolve(postUser)
    expect(await positiveOutcome).toEqual({ ok: true })
    await store.whenIdle()
    await notifications()
    const saved = snapshot()
    expect(saved).toMatchObject({
      state: 'closed-success', provenance: 'saved', writable: true,
      pending: false, readPending: false, openedBaseline: null, draft: null, layout: reply.layout,
    })
    expect(saved.canUndo).toBe(false)
    expect(saved.counters).toEqual(succeeded)
    expect(saved.overlay).toEqual({ [A]: reply.layout })
    expect(request.binding.isCurrent()).toBe(false)
    expect(snapshot(B).layout).toEqual(sibling)
    expect(authSeed).toEqual(seedBefore)
    expect(updateMe).not.toHaveBeenCalled()

    phase = 'D discard, held READ preservation, and strict revoke'
    const discardId = store.open(A)
    expect(discardId).not.toBe(positiveId)
    store.setDraft(discardId, S0.items)
    reset(discardId)
    expect(snapshot().canUndo).toBe(true)
    store.discard(discardId)
    expect(snapshot()).toMatchObject({ state: 'closed', openedBaseline: null, draft: null, layout: reply.layout })
    expect(snapshot().canUndo).toBe(false)
    expect(snapshot().overlay).toEqual({ [A]: reply.layout })
    expect(snapshot().counters).toEqual(succeeded)
    const discarded = snapshot()
    expect(() => reset(discardId)).toThrowError(new Error(editableError))
    expect(() => undoReset(discardId)).toThrowError(new Error(editableError))
    expect(snapshot()).toBe(discarded)
    expect(me).toHaveBeenCalledTimes(2)
    expect(saveLayout).toHaveBeenCalledTimes(1)
    const readId = store.open(A)
    expect(readId).not.toBe(discardId)
    expect(snapshot().canUndo).toBe(false)
    store.setDraft(readId, S0.items)
    reset(readId)
    expect(snapshot().canUndo).toBe(true)
    const beforeRead = snapshot()
    const readOutcome = observe(store.refresh(A))
    const reading = snapshot()
    expect(reading.readPending).toBe(true)
    expect(reading.canUndo).toBe(false)
    expect(reading.openedBaseline).toEqual(beforeRead.openedBaseline)
    expect(reading.draft).toEqual(beforeRead.draft)
    expect(reading.overlay).toEqual({ [A]: reply.layout })
    expect(reading.counters).toEqual(succeeded)
    expect(store.retain(A)).toBe(readId)
    expect(() => reset(readId)).toThrowError(new Error(editableError))
    expect(() => undoReset(readId)).toThrowError(new Error(editableError))
    expect(snapshot()).toBe(reading)
    await notifications()
    expect(observed.at(-1)).toBe(reading)
    expect(me).toHaveBeenCalledTimes(3)
    expect(saveLayout).toHaveBeenCalledTimes(1)
    heldGet.resolve(postUser)
    expect(await readOutcome).toEqual({ ok: true })
    await store.whenIdle()
    await notifications()
    expect(snapshot().canUndo).toBe(true)
    expect(snapshot().openedBaseline).toEqual(beforeRead.openedBaseline)
    expect(snapshot().draft).toEqual(beforeRead.draft)
    undoReset(readId) // Public proof that the READ disabled but did not erase the slot.
    expect(snapshot().draft).toEqual(S0)
    expect(snapshot().canUndo).toBe(false)
    reset(readId)
    expect(snapshot().canUndo).toBe(true)
    store.revoke()
    const revokedA = snapshot(A)
    const revokedB = snapshot(B)
    for (const workspaceId of [A, B]) {
      const revoked = snapshot(workspaceId)
      expect(revoked.overlay).toEqual({})
      expect(revoked.canUndo).toBe(false)
      expect(revoked.openedBaseline).toBeNull()
      expect(revoked.draft).toBeNull()
      expect(revoked).toMatchObject({ state: 'closed', writable: false, pending: false, readPending: false })
      expect(revoked.counters).toEqual(succeeded)
    }
    expect(() => reset(readId)).toThrowError(new Error(editableError))
    expect(() => undoReset(readId)).toThrowError(new Error(editableError))
    expect(() => reset(discardId)).toThrowError(new Error(editableError))
    expect(() => undoReset(discardId)).toThrowError(new Error(editableError))
    expect(snapshot(A)).toBe(revokedA)
    expect(snapshot(B)).toBe(revokedB)
    await store.whenIdle()
    await notifications()
    expect(me).toHaveBeenCalledTimes(3)
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(captured).toHaveLength(1)
    expect(updateMe).not.toHaveBeenCalled()
    expect(observerErrors).toEqual([])
    expect(authSeed).toEqual(seedBefore)
    expect(entryUser).toEqual(entryBefore)
    expect(postUser).toEqual(postBefore)
    expect(reply).toEqual(replyBefore)
    expect(authSeed.preferences.dashboard_layouts?.[B]).toEqual(sibling)
    expect(postUser.preferences.dashboard_layouts?.[B]).toEqual(sibling)
    expect(DASHBOARD_WIDGETS).toEqual(registryBefore)
  } catch (cause: unknown) {
    primary = { phase, cause }
  } finally {
    // Release every possible input before any full native idle wait, even on RED.
    entryGet.resolve(entryUser)
    put.resolve(reply)
    postGet.resolve(postUser)
    heldGet.resolve(postUser)
    try {
      const outcomes = await Promise.all(issued)
      outcomes.forEach((outcome) => { if (!outcome.ok) cleanupErrors.push(outcome.error) })
      await store.whenIdle()
    } catch (error: unknown) { cleanupErrors.push(error) }
    try { unsubscribe() } catch (error: unknown) { cleanupErrors.push(error) }
    try { store.revoke(); await notifications() } catch (error: unknown) { cleanupErrors.push(error) }
  }
  if (primary) {
    const body = new Error(`Primary body phase: ${primary.phase}`, { cause: primary.cause })
    throw new AggregateError([body, ...cleanupErrors], 'Reset/undo body failed; cleanup diagnostics follow primary', { cause: primary.cause })
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'Reset/undo cleanup failed')
})
