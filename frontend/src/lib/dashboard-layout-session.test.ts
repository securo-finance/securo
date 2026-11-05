import { expect, it, vi } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  moveItem,
  resolveDashboardLayout,
  toPersistedLayout,
  toggleItem,
  validateCommittedDashboardLayout,
  type CommittedDashboardLayout,
  type DashboardLayout,
  type DashboardLayoutItem,
  type DashboardRequestBinding,
  type DashboardWidgetSpec,
} from '@/lib/dashboard-layout'
import type { User } from '@/types'

// Proposed INTERNAL port, not an assertion that these exports already exist.
// One factory call represents one provider-lifetime owner, never a global cache.
interface SessionSnapshot {
  actorId: string
  authGeneration: number
  workspaceId: string
  provenance: 'default' | 'saved' | 'foreign'
  writable: boolean
  state: 'closed' | 'editing' | 'pending' | 'closed-success'
  pending: boolean
  readPending: boolean
  layout: DashboardLayout
  openedBaseline: DashboardLayout | null
  draft: DashboardLayout | null
  overlay: Readonly<Record<string, DashboardLayout>>
  counters: Readonly<{
    success: number
    suppressed: number
    conflict409: number
    other4xx: number
    server5xx: number
  }>
}
interface SessionStore {
  // Explicit entry read: factory seeds display, refresh settles authoritative GET.
  refresh(workspaceId: string): Promise<void>
  open(workspaceId: string): string
  setDraft(sessionId: string, items: readonly DashboardLayoutItem[]): void
  // Synchronous latch/pending transition; promise covers completion + recheck.
  finalize(sessionId: string): Promise<void>
  // Includes queued transitions, reads, completion callbacks and their follow-ups.
  whenIdle(): Promise<void>
  getSnapshot(workspaceId: string): SessionSnapshot
}
interface StoreOptions {
  registry: readonly DashboardWidgetSpec[]
  actorId: string
  authGeneration: number
  authSeed: Readonly<User>
  getTokenIdentity(): string | null
  getWorkspaceId(): string
  api: {
    me(): Promise<User>
    saveLayout(
      workspaceId: string,
      layout: DashboardLayout,
      binding: DashboardRequestBinding,
    ): Promise<CommittedDashboardLayout>
  }
}
interface StoreModule {
  createDashboardLayoutSession(options: StoreOptions): SessionStore
}

// Installed Vite importGlob.d.ts:118–121 supports this lazy generic overload.
// No static import of the nonexistent product; empty discovery fails IN the test.
const storeModules = import.meta.glob<StoreModule>('./dashboard-layout-session.ts')
const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}

// Fixture immutability only: no normalization, store transition or writer logic.
function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}

it('one session store suppresses unchanged default close then commits one scoped changed layout without replacing auth or siblings', async () => {
  const loadStore = storeModules['./dashboard-layout-session.ts']
  expect(typeof loadStore, 'real dashboard-layout-session module must exist before any behavioral proof').toBe('function')

  const defaults = resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, A)
  const sibling = toPersistedLayout(
    B,
    toggleItem(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, B).items, 'goals-progress', false),
    DASHBOARD_WIDGETS,
  )
  const authSeed: User = freezeFixture({
    id: ACTOR,
    email: 'store-tracer@example.invalid',
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR',
      date_format: 'YYYY-MM-DD',
      timezone: 'UTC',
      currency_display: 'symbol',
      display_name: 'Synthetic tracer',
      onboarding_completed: true,
      dashboard_layouts: { [B]: sibling },
    },
  })
  const seedBefore = structuredClone(authSeed)
  const preferencesBefore = authSeed.preferences
  const siblingBefore = structuredClone(sibling)
  const initialRead = deferred<User>()
  const putReply = deferred<CommittedDashboardLayout>()
  const putStarted = deferred<void>()
  let postCommitRead: User | undefined
  // Kept exclusively at the boundary getter; never a record/counter fixture field.
  let liveToken: string | null = 'synthetic-store-tracer-token'
  let liveWorkspace = A
  const me = vi.fn<() => Promise<User>>(() => {
    if (me.mock.calls.length === 1) return initialRead.promise
    if (!postCommitRead) throw new Error('Unexpected GET before explicit committed fixture')
    return Promise.resolve(postCommitRead)
  })
  const captured: Array<{
    workspaceId: string
    layout: DashboardLayout
    binding: DashboardRequestBinding
  }> = []
  const saveLayout = vi.fn<StoreOptions['api']['saveLayout']>((workspaceId, layout, binding) => {
    captured.push({ workspaceId, layout: structuredClone(layout), binding })
    putStarted.resolve()
    return putReply.promise
  })
  // Forbidden legacy API boundary, visible even if product imports it directly.
  const updateMe = vi.fn<(...args: unknown[]) => Promise<User>>(() => {
    throw new Error('Forbidden generic auth.updateMe in dashboard session store')
  })

  vi.resetModules()
  vi.doMock('@/lib/api', () => ({ auth: { me, updateMe }, dashboard: { saveLayout } }))
  try {
    const module = await loadStore()
    expect(typeof module.createDashboardLayoutSession, 'real session factory must exist').toBe('function')
    const store = module.createDashboardLayoutSession({
      registry: DASHBOARD_WIDGETS,
      actorId: ACTOR,
      authGeneration: 1,
      authSeed,
      getTokenIdentity: () => liveToken,
      getWorkspaceId: () => liveWorkspace,
      api: { me, saveLayout },
    })
    for (const method of ['refresh', 'open', 'setDraft', 'finalize', 'whenIdle', 'getSnapshot'] as const) {
      expect(typeof store[method], `real store.${method} must exist`).toBe('function')
    }
    expect(store.getSnapshot(A).layout).toEqual(defaults)
    const refresh = store.refresh(A)
    expect(me).toHaveBeenCalledTimes(1)
    expect(store.getSnapshot(A).readPending).toBe(true)
    // Literal actor-matching initial GET: missing A, saved B, unchanged preferences.
    initialRead.resolve(freezeFixture(structuredClone(seedBefore)))
    await refresh
    await store.whenIdle()
    const initial = store.getSnapshot(A)
    expect(initial).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A,
      provenance: 'default', writable: true, state: 'closed',
      pending: false, readPending: false, layout: defaults,
      counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    const unchangedSession = store.open(A)
    expect(typeof unchangedSession).toBe('string')
    expect(store.getSnapshot(A)).toMatchObject({
      state: 'editing', openedBaseline: defaults, draft: defaults,
    })
    await store.finalize(unchangedSession)
    await store.whenIdle()
    const unchanged = store.getSnapshot(A)
    expect(unchanged).toMatchObject({ state: 'closed', pending: false, readPending: false })
    expect(unchanged.counters).toEqual({ success: 0, suppressed: 1, conflict409: 0, other4xx: 0, server5xx: 0 })
    expect(saveLayout).not.toHaveBeenCalled()
    expect(updateMe).not.toHaveBeenCalled()
    expect(unchanged.overlay).toEqual({})
    expect(authSeed).toEqual(seedBefore)
    expect(authSeed.preferences).toBe(preferencesBefore)
    expect(store.getSnapshot(B).layout).toEqual(siblingBefore)

    // Mandatory positive control on the SAME owner, actor, generation and target.
    const changedSession = store.open(A)
    expect(changedSession).not.toBe(unchangedSession)
    const editedItems = toggleItem(moveItem(defaults.items, 0, 2), 'goals-progress', false)
    const expected = toPersistedLayout(A, editedItems, DASHBOARD_WIDGETS)
    expect(expected).not.toEqual(defaults)
    expect(expected.items.map((item) => item.id)).toEqual([
      'uncategorized-banner', 'spending-charts', 'hero-balance',
      'balance-evolution', 'goals-progress', 'period-transactions',
    ])
    expect(expected.items.find((item) => item.id === 'goals-progress')?.visible).toBe(false)
    store.setDraft(changedSession, editedItems)
    expect(store.getSnapshot(A).draft).toEqual(expected)
    expect(saveLayout).not.toHaveBeenCalled()
    const completion = store.finalize(changedSession)
    expect(completion).toBeInstanceOf(Promise)
    expect(store.getSnapshot(A)).toMatchObject({ state: 'pending', pending: true })
    const duplicateCompletion = store.finalize(changedSession)
    expect(duplicateCompletion).toBeInstanceOf(Promise)
    await putStarted.promise
    const pending = store.getSnapshot(A)
    expect(pending).toMatchObject({ state: 'pending', pending: true })
    expect(pending.counters.success).toBe(0)
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(captured).toHaveLength(1)
    const request = captured[0]
    expect(request.workspaceId).toBe(A)
    expect(request.layout).toEqual(expected)
    expect(Object.keys(request.layout).sort()).toEqual(['items', 'version', 'workspaceId'])
    request.layout.items.forEach((item, order) => {
      expect(Object.keys(item).sort()).toEqual(['id', 'order', 'visible'])
      expect(item.order).toBe(order)
    })
    expect(typeof request.binding.isCurrent).toBe('function')
    expect(request.binding.isCurrent()).toBe(true)
    expect(updateMe).not.toHaveBeenCalled()

    // Explicit fixtures, NOT a fake server writer or preferences merge algorithm.
    // Build the response independently from the captured request object.
    const committed = validateCommittedDashboardLayout(A, {
      workspaceId: A,
      layout: structuredClone(expected),
    }, DASHBOARD_WIDGETS)
    postCommitRead = freezeFixture({
      id: ACTOR,
      email: 'store-tracer@example.invalid',
      is_active: true,
      is_superuser: false,
      is_verified: true,
      is_2fa_enabled: false,
      preferences: {
        language: 'pt-BR',
        date_format: 'YYYY-MM-DD',
        timezone: 'UTC',
        currency_display: 'symbol',
        display_name: 'Synthetic tracer',
        onboarding_completed: true,
        dashboard_layouts: { [A]: structuredClone(expected), [B]: structuredClone(siblingBefore) },
      },
    })
    putReply.resolve(committed)
    await Promise.all([completion, duplicateCompletion])
    await store.whenIdle()
    const completed = store.getSnapshot(A)
    expect(completed).toMatchObject({
      actorId: ACTOR, authGeneration: 1, workspaceId: A,
      state: 'closed-success', pending: false, readPending: false, layout: expected,
    })
    expect(completed.counters).toEqual({ success: 1, suppressed: 1, conflict409: 0, other4xx: 0, server5xx: 0 })
    expect(Object.keys(completed.counters).sort()).toEqual(['conflict409', 'other4xx', 'server5xx', 'success', 'suppressed'])
    expect(completed.overlay).toEqual({ [A]: expected })
    expect(store.getSnapshot(B).layout).toEqual(siblingBefore)
    expect(authSeed).toEqual(seedBefore)
    expect(authSeed.preferences).toBe(preferencesBefore)
    expect(authSeed.preferences.dashboard_layouts?.[B]).toBe(sibling)
    expect(authSeed.preferences.dashboard_layouts).not.toHaveProperty(A)
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(updateMe).not.toHaveBeenCalled()
    expect(captured).toHaveLength(1)
  } finally {
    // File-local mock registration restoration only; no product cleanup proof.
    vi.doUnmock('@/lib/api')
    vi.restoreAllMocks()
    vi.resetModules()
    liveToken = null
    liveWorkspace = A
  }
})
