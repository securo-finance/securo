import { useLayoutEffect, useState, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'

import { CustomizeDashboardDialog } from '@/components/dashboard/customize-dashboard-dialog'
import { useDashboardLayout, type DashboardLayoutController } from '@/hooks/use-dashboard-layout'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { createTestQueryClient, renderWithProviders, t } from '@/test/utils'
import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { getDashboardAuthSnapshot } from '@/lib/dashboard-layout-auth-bridge'
import { getPageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import { validateCommittedDashboardLayout, validateDashboardLayoutRequest,
  type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding } from '@/lib/dashboard-layout'
import type { SessionSnapshot, SnapshotLayout } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

const mocks = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout, binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  toastError: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: mocks.me, updateMe: mocks.updateMe },
  dashboard: { saveLayout: mocks.saveLayout },
}))
vi.mock('sonner', () => ({
  toast: { error: (message: unknown) => mocks.toastError(message) },
}))

const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'
const TOKEN = 'synthetic-dialog-tracer-token'
function makeUser(preferences: User['preferences']): User {
  return {
    id: ACTOR,
    email: 'dialog@example.invalid',
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_2fa_enabled: false,
    preferences,
  }
}

// Test-only views of the design proposal; no production recovery exports/ports.
type RecoveryRead = Readonly<{ readId: number; provenance: 'default' | 'saved' | 'foreign';
  target: SnapshotLayout; matchesSubmitted: boolean }>
type Recovery = Readonly<{ attemptId: string; actorId: string; authGeneration: number;
  workspaceId: string; epoch: number; baseline: SnapshotLayout; submitted: SnapshotLayout;
  category: 'conflict' | 'unknown' | 'blocked' | 'correction-required';
  phase: 'needs-recheck' | 'rechecking' | 'review-ready' | 'protected' | 'recheck-failed' | 'matched';
  recheck: RecoveryRead | null }>
type RecoverySnapshot = SessionSnapshot & Partial<{ readonly recovery: Recovery | null }>
type RecoveryController = DashboardLayoutController & Partial<{ readonly recovery: Recovery | null }>

// Literal committed envelope and independent postGET document, never request echoes.
const firstReply: CommittedDashboardLayout = { workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} }
const firstPost = makeUser({ dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} } })
const secondDraft: DashboardLayout = { version: 1, workspaceId: A, items: [
  { id: 'uncategorized-banner', visible: true, order: 0 },
  { id: 'hero-balance', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'period-transactions', visible: true, order: 4 },
  { id: 'goals-progress', visible: true, order: 5 },
] }
function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}
;[firstReply, firstPost, secondDraft].forEach((value) => { freezeFixture(value) })
function workspace(id: string): Workspace {
  return { id, name: 'Synthetic dialog', kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null, icon: null, color: null,
    created_at: '2026-10-05T00:00:00Z', created_by_user_id: ACTOR, managed_by_user_id: null,
    role: 'owner', enabled_modules: ['accounts', 'transactions', 'goals'] }
}
function required<T>(value: T | null, label: string): T {
  if (value === null) throw new Error(`Missing real dialog witness: ${label}`)
  return value
}
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })
function deferred<T>(value: T) {
  let resolve!: (value: T) => void
  let reject!: (failure: unknown) => void
  let settled = false
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { value, promise, issued: false,
    settle() { if (!settled) { settled = true; resolve(value) } },
    fail(failure: unknown) { if (!settled) { settled = true; reject(failure) } },
  }
}

/** Only the real hook owns a page binding; observing adds no lease holder. */
function Harness({ observe }: { observe: (layout: DashboardLayoutController | null) => void }) {
  const layout = useDashboardLayout()
  useLayoutEffect(() => { observe(layout) })
  useLayoutEffect(() => () => { observe(null) }, [observe])
  return (
    <>
      <button
        type="button"
        data-testid="open-customize"
        onClick={layout.openCustomize}
      >
        open
      </button>
      <CustomizeDashboardDialog
        open={layout.customizeOpen}
        onOpenChange={(next) =>
          next ? layout.openCustomize() : layout.closeCustomize(true)
        }
        draft={layout.draft}
        canUndo={layout.canUndo}
        onReorder={layout.reorder}
        onMove={layout.move}
        onToggle={layout.toggleVisible}
        onReset={layout.reset}
        onUndo={layout.undoReset}
      />
    </>
  )
}

async function openDialog() {
  const result = h.result()
  await result.user.click(screen.getByTestId('open-customize'))
  await h.flush()
  return result
}

function lifetimeHarness() {
  const client = createTestQueryClient(), seed = freezeFixture(makeUser({}))
  const a = workspace(A), b = workspace(B)
  const reads: ReturnType<typeof deferred<User>>[] = []
  const puts: ReturnType<typeof deferred<CommittedDashboardLayout>>[] = []
  const tasks: Promise<void>[] = [], unexpected: unknown[] = [], publications: unknown[] = []
  const gets: Array<{ completed: boolean; failure: unknown; user: User | null }> = []
  const requests: Array<{ id: string; layout: DashboardLayout; binding: DashboardRequestBinding;
    current: boolean; generation: number; storageTarget: string | null }> = []
  const owners = new Set<DashboardLayoutOwner>(), unsubscribes: Array<() => void> = []
  const writers: Array<ReturnType<typeof vi.spyOn>> = [], contexts = new WeakSet<object>()
  const state: { owner: DashboardLayoutOwner | null; hook: DashboardLayoutController | null;
    auth: ReturnType<typeof useAuth> | null; show: ((value: boolean) => void) | null } = {
    owner: null, hook: null, auth: null, show: null,
  }
  let cleaning = false
  let mounted: ReturnType<typeof renderWithProviders> | null = null
  const storage = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  const observe = (layout: DashboardLayoutController | null) => { state.hook = layout }
  function track(task: Promise<unknown>) {
    tasks.push(task.then(() => {}, (failure: unknown) => { unexpected.push(failure) }))
  }
  const read = (user: User) => { const port = deferred(user); reads.push(port); return port }
  const put = (reply: CommittedDashboardLayout) => {
    expect(validateCommittedDashboardLayout(A, reply, DASHBOARD_WIDGETS)).toStrictEqual(reply)
    const port = deferred(reply); puts.push(port); return port
  }
  mocks.me.mockReset().mockImplementation(() => {
    const call = { completed: false, failure: null as unknown, user: null as User | null }
    gets.push(call)
    const port = reads[gets.length - 1]
    if (!port) {
      const failure = new Error('Unexpected extra dialog GET'); unexpected.push(failure)
      return Promise.reject(failure)
    }
    port.issued = true
    // Observe transport rejection without asserting/throwing in production callbacks.
    tasks.push(port.promise.then((user) => { call.user = user; call.completed = true },
      (failure: unknown) => { call.failure = failure; call.completed = true }))
    if (cleaning) port.settle()
    return port.promise
  })
  mocks.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    requests.push({ id, layout: structuredClone(layout), binding, current: binding.isCurrent(),
      generation: getDashboardAuthSnapshot(client).generation,
      storageTarget: localStorage.getItem(WORKSPACE_STORAGE_KEY) })
    const port = puts[requests.length - 1]
    if (!port) {
      const failure = new Error('Unexpected extra dialog PUT'); unexpected.push(failure)
      return Promise.reject(failure)
    }
    port.issued = true
    tasks.push(port.promise.then(() => {}, () => {}))
    if (cleaning) port.settle()
    return port.promise
  })
  mocks.updateMe.mockReset().mockImplementation(() => {
    const failure = new Error('Forbidden generic dialog writer'); unexpected.push(failure)
    return Promise.reject(failure)
  })
  function ObserveAuth({ children }: { children: ReactNode }) {
    const context = useAuth()
    if (!contexts.has(context)) { contexts.add(context); writers.push(vi.spyOn(context, 'updateUser')) }
    useLayoutEffect(() => { state.auth = context })
    return children
  }
  function Placement() {
    const owner = useDashboardLayoutOwner()
    const [show, setShow] = useState(true)
    useLayoutEffect(() => {
      state.owner = owner; state.show = setShow
      if (owner && !owners.has(owner)) {
        owners.add(owner)
        unsubscribes.push(owner.store.subscribe(() => {
          try { publications.push([owner.store.getSnapshot(A), owner.store.getSnapshot(B)]) }
          catch (failure: unknown) { unexpected.push(failure) }
        }))
      }
    }, [owner])
    return show ? <Harness observe={observe} /> : null
  }
  function Shell() {
    return <AuthProvider><ObserveAuth><WorkspaceContext.Provider value={{ current: a,
      workspaces: [a, b], isLoading: false, role: 'owner', canManage: true, canWrite: true,
      enabledModules: ['accounts', 'transactions', 'goals'], hasModule: (id) => a.enabled_modules.includes(id),
      switchWorkspace: async () => { throw new Error('Unexpected workspace switch') },
      refresh: async () => { throw new Error('Unexpected workspace refresh') } }}>
      <DashboardLayoutProvider><Placement /></DashboardLayoutProvider>
    </WorkspaceContext.Provider></ObserveAuth></AuthProvider>
  }
  async function flush() { await act(async () => { await tick(); await tick() }) }
  async function drain() {
    for (let turn = 0; turn < 8; turn += 1) {
      const before = [tasks.length, publications.length, gets.length, requests.length, owners.size]
      await tick(); await tick(); await Promise.all(tasks)
      for (const owner of owners) {
        await owner.store.whenIdle(); await getPageSessionLease(owner.store).whenIdle()
      }
      await tick(); await tick(); await Promise.all(tasks)
      const after = [tasks.length, publications.length, gets.length, requests.length, owners.size]
      if (after.every((value, index) => value === before[index])) return
    }
    throw new Error('Dialog work did not reach native publication fixed point')
  }
  async function settle(...ports: Array<{ settle(): void }>) {
    await act(async () => { ports.forEach((port) => { port.settle() }); await drain() })
    await act(async () => { await drain() })
  }
  const hook = () => required(state.hook, 'hook')
  const owner = () => required(state.owner, 'owner')
  function stable(expected: DashboardLayoutOwner) {
    expect(state.owner).toBe(expected); expect([...owners]).toEqual([expected])
    expect(expected.actorId).toBe(ACTOR)
    expect(expected.authGeneration).toBe(getDashboardAuthSnapshot(client).generation)
    expect(state.auth?.user).toBe(seed); expect(state.auth?.token).toBe(TOKEN)
    expect(localStorage.getItem('token')).toBe(TOKEN)
    expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(A)
    expect(required(mounted, 'mounted client').queryClient).toBe(client)
  }
  function evidence() {
    expect(unexpected).toEqual([]); expect(mocks.updateMe).not.toHaveBeenCalled()
    expect(writers.length).toBeGreaterThan(0)
    writers.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    expect(gets.every((call) => call.completed)).toBe(true)
    gets.forEach((call) => { if (call.user) expect(call.user.id).toBe(ACTOR) })
    expect(client.isMutating()).toBe(0)
  }
  function request(index: number, expected: DashboardLayout, captured: DashboardLayoutOwner) {
    const call = requests[index]!
    expect(call.id).toBe(A); expect(call.layout).toStrictEqual(expected)
    expect(call.current).toBe(true); expect(call.binding.isCurrent()).toBe(true)
    expect(call.generation).toBe(captured.authGeneration); expect(call.storageTarget).toBe(A)
    expect(Reflect.ownKeys(call.layout).sort()).toEqual(['items', 'version', 'workspaceId'])
    call.layout.items.forEach((item) => {
      expect(Reflect.ownKeys(item).sort()).toEqual(['id', 'order', 'visible'])
    })
    expect(validateDashboardLayoutRequest(A, call.layout, DASHBOARD_WIDGETS)).toStrictEqual(expected)
  }
  async function start() {
    localStorage.setItem('token', TOKEN); localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
    const restoration = read(seed), entry = read(seed)
    mounted = renderWithProviders(<Shell />, { queryClient: client, reactStrictMode: false })
    await settle(restoration, entry)
    expect(gets).toHaveLength(2); expect(gets.map((call) => call.user)).toEqual([seed, seed])
    expect(owner().store.getSnapshot(A)).toMatchObject({ writable: true, readPending: false })
    stable(owner()); evidence()
  }
  async function show(value: boolean) {
    await act(async () => { required(state.show, 'child placement')(value); await tick() })
    await flush()
  }
  async function cleanup() {
    const errors: unknown[] = []
    async function step(action: () => void | Promise<void>) {
      try { await action() } catch (failure: unknown) { errors.push(failure) }
    }
    cleaning = true
    // Resolve issued ports only; a newly issued postGET is settled by its boundary.
    reads.filter((port) => port.issued).forEach((port) => { port.settle() })
    puts.filter((port) => port.issued).forEach((port) => { port.settle() })
    await step(async () => {
      await act(async () => { state.hook?.closeCustomize(false); await drain() })
    })
    await step(async () => { await show(false); await settle() })
    await step(async () => { await act(async () => { mounted?.unmount(); await drain() }) })
    await step(() => {
      owners.forEach(({ store }) => {
        for (const id of [A, B]) {
          expect(store.getSnapshot(id)).toMatchObject({ writable: false, pending: false,
            readPending: false, openedBaseline: null, draft: null })
          expect(store.getSnapshot(id).overlay).toStrictEqual({})
        }
      })
      evidence()
    })
    unsubscribes.forEach((unsubscribe) => { unsubscribe() })
    await step(() => { client.clear() })
    await step(async () => { await act(async () => { await drain() }) })
    writers.forEach((spy) => { spy.mockRestore() })
    storage.forEach(({ key, value }) => {
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value)
    })
    // Keep call evidence through afterEach; the next fixture resets all ports.
    return errors
  }
  return { start, cleanup, result: () => required(mounted, 'render'), hook, owner,
    read, put, settle, flush, drain, show, stable, evidence, request, track, gets, requests }
}
let h: ReturnType<typeof lifetimeHarness>

/** Widget names in the order the rows currently render. */
function rowNames(): (string | null)[] {
  return screen
    .getAllByRole('listitem')
    .map((row) => row.querySelector('.truncate')?.textContent ?? null)
}

const REGISTRY_NAMES = DASHBOARD_WIDGETS.map((definition) =>
  t(definition.translationKey),
)

function nameOf(id: string): string {
  const definition = DASHBOARD_WIDGETS.find((d) => d.id === id)
  return t(definition!.translationKey)
}

function handleFor(id: string): HTMLElement {
  return screen.getByRole('button', {
    name: t('dashboard.customize.dragHandle', { name: nameOf(id) }),
  })
}

beforeEach(async () => {
  vi.clearAllMocks()
  h = lifetimeHarness()
  await h.start()
})
afterEach(async ({ task }) => {
  const errors = await h.cleanup()
  // Preserve the original assertion failure; cleanup-only failures escape teardown.
  if (errors.length && task.result?.state === 'fail') {
    console.error('Dialog cleanup diagnostics; primary body failure preserved', errors)
  } else if (errors.length) throw new AggregateError(errors, 'Dialog cleanup failed')
  if (!task.name.startsWith('closing the panel persists')) {
    expect(h.requests).toEqual([]); expect(mocks.saveLayout).not.toHaveBeenCalled()
  }
})

describe('CustomizeDashboardDialog', () => {
  it('bounds the panel height and allows scrolling to controls on short touch viewports', async () => {
    await openDialog()

    // jsdom cannot measure layout; the real-browser landscape regression
    // additionally verifies the footer can be scrolled into view and tapped.
    expect(screen.getByRole('dialog')).toHaveClass(
      'max-h-[calc(100dvh-2rem)]',
      'overflow-y-auto',
    )
  })

  it('renders one row per registered widget in the current draft order', async () => {
    const { user } = await openDialog()

    expect(screen.getAllByRole('listitem')).toHaveLength(
      DASHBOARD_WIDGETS.length,
    )
    expect(rowNames()).toEqual(REGISTRY_NAMES)

    // The draft drives the rows: move the first widget down and the row
    // order follows.
    const heroDown = screen.getByRole('button', {
      name: t('dashboard.customize.moveDown', { name: nameOf('hero-balance') }),
    })
    await user.click(heroDown)
    expect(rowNames()).toEqual([
      nameOf('uncategorized-banner'),
      nameOf('hero-balance'),
      nameOf('spending-charts'),
      nameOf('balance-evolution'),
      nameOf('goals-progress'),
      nameOf('period-transactions'),
    ])
  })

  it('move up and move down buttons are disabled at the list boundaries and reorder rows between them', async () => {
    const { user } = await openDialog()

    // Boundaries: first row cannot move up, last row cannot move down.
    expect(
      screen.getByRole('button', {
        name: t('dashboard.customize.moveUp', { name: nameOf('hero-balance') }),
      }),
    ).toBeDisabled()
    expect(
      screen.getByRole('button', {
        name: t('dashboard.customize.moveDown', {
          name: nameOf('period-transactions'),
        }),
      }),
    ).toBeDisabled()
    expect(
      screen.getByRole('button', {
        name: t('dashboard.customize.moveDown', { name: nameOf('hero-balance') }),
      }),
    ).toBeEnabled()
    expect(
      screen.getByRole('button', {
        name: t('dashboard.customize.moveUp', {
          name: nameOf('period-transactions'),
        }),
      }),
    ).toBeEnabled()

    // Interior move: banner (row 2) up swaps with hero.
    await user.click(
      screen.getByRole('button', {
        name: t('dashboard.customize.moveUp', {
          name: nameOf('uncategorized-banner'),
        }),
      }),
    )
    expect(rowNames()).toEqual([
      nameOf('uncategorized-banner'),
      nameOf('hero-balance'),
      nameOf('spending-charts'),
      nameOf('balance-evolution'),
      nameOf('goals-progress'),
      nameOf('period-transactions'),
    ])

    // And back down.
    await user.click(
      screen.getByRole('button', {
        name: t('dashboard.customize.moveDown', {
          name: nameOf('uncategorized-banner'),
        }),
      }),
    )
    expect(rowNames()).toEqual(REGISTRY_NAMES)
  })

  it('arrow up and arrow down on the drag handle reorder and announce the new position', async () => {
    await openDialog()

    const bannerHandle = handleFor('uncategorized-banner')
    await act(async () => {
      bannerHandle.focus()
      fireEvent.keyDown(bannerHandle, { key: 'ArrowUp' })
      await h.drain()
    })
    expect(rowNames()[0]).toBe(nameOf('uncategorized-banner'))
    expect(screen.getByText(
      t('dashboard.customize.announceMoved', {
        name: nameOf('uncategorized-banner'),
        position: 1,
        total: DASHBOARD_WIDGETS.length,
      }),
    )).toBeInTheDocument()

    await act(async () => {
      fireEvent.keyDown(bannerHandle, { key: 'ArrowDown' })
      await h.drain()
    })
    expect(rowNames()[1]).toBe(nameOf('uncategorized-banner'))
    expect(screen.getByText(
      t('dashboard.customize.announceMoved', {
        name: nameOf('uncategorized-banner'),
        position: 2,
        total: DASHBOARD_WIDGETS.length,
      }),
    )).toBeInTheDocument()

    // Clamped at the boundary: no wrap, no announcement of a fake move.
    await act(async () => {
      fireEvent.keyDown(handleFor('hero-balance'), { key: 'ArrowUp' })
      await h.drain()
    })
    expect(rowNames()).toEqual(REGISTRY_NAMES)
    expect(screen.getByText(
      t('dashboard.customize.announceMoved', {
        name: nameOf('uncategorized-banner'),
        position: 2,
        total: DASHBOARD_WIDGETS.length,
      }),
    )).toBeInTheDocument()
  })

  it("keyboard reorder keeps focus on the moved widget's handle", async () => {
    await openDialog()

    const bannerHandle = handleFor('uncategorized-banner')
    await act(async () => {
      bannerHandle.focus()
      fireEvent.keyDown(bannerHandle, { key: 'ArrowUp' })
      await h.drain()
    })

    // The keyed reorder moves DOM nodes (which normally blurs) — focus must
    // land back on the same widget's handle.
    expect(document.activeElement).toBe(handleFor('uncategorized-banner'))
    expect(document.activeElement).toBe(bannerHandle)
  })

  it('hiding a widget does not reorder the list and hiding the last visible widget is refused with a hint', async () => {
    const { user } = await openDialog()

    // Hide one widget: visibility flips, order does not move.
    await user.click(
      screen.getByRole('switch', {
        name: t('dashboard.customize.hide', { name: nameOf('hero-balance') }),
      }),
    )
    expect(rowNames()).toEqual(REGISTRY_NAMES)
    expect(
      screen.getByRole('switch', {
        name: t('dashboard.customize.show', { name: nameOf('hero-balance') }),
      }),
    ).toHaveAttribute('aria-checked', 'false')

    // Hide everything except the last visible widget.
    for (const id of [
      'uncategorized-banner',
      'spending-charts',
      'balance-evolution',
      'goals-progress',
    ]) {
      await user.click(
        screen.getByRole('switch', {
          name: t('dashboard.customize.hide', { name: nameOf(id) }),
        }),
      )
    }
    expect(rowNames()).toEqual(REGISTRY_NAMES)

    // Refuse the final hide: switch stays on, hint appears, live region
    // repeats it (§7.5).
    await user.click(
      screen.getByRole('switch', {
        name: t('dashboard.customize.hide', {
          name: nameOf('period-transactions'),
        }),
      }),
    )
    expect(
      screen.getByRole('switch', {
        name: t('dashboard.customize.hide', {
          name: nameOf('period-transactions'),
        }),
      }),
    ).toHaveAttribute('aria-checked', 'true')
    // §7.5: the inline hint (role=status) appears AND the polite live
    // region repeats the rule. (dnd-kit renders its own role=status live
    // region, so scope by content + role rather than getByRole('status').)
    const hintText = t('dashboard.customize.lastVisible')
    const repeated = screen.getAllByText(hintText)
    expect(repeated).toHaveLength(2)
    expect(
      repeated.some((el) => el.getAttribute('role') === 'status'),
    ).toBe(true)
    expect(rowNames()).toEqual(REGISTRY_NAMES)
  })

  it('reset restores registry defaults and undo restores the pre-reset draft', async () => {
    const { user } = await openDialog()

    // Move a widget so the draft differs from defaults.
    await user.click(
      screen.getByRole('button', {
        name: t('dashboard.customize.moveUp', {
          name: nameOf('period-transactions'),
        }),
      }),
    )
    expect(rowNames()).not.toEqual(REGISTRY_NAMES)

    // Reset: registry defaults back (period returned to its slot).
    await user.click(
      screen.getByRole('button', { name: t('dashboard.customize.reset') }),
    )
    expect(rowNames()).toEqual(REGISTRY_NAMES)
    expect(screen.getByText(
      t('dashboard.customize.announceReset'),
    )).toBeInTheDocument()

    // Footer swapped to Undo; undoing restores the exact pre-reset draft.
    expect(
      screen.queryByRole('button', { name: t('dashboard.customize.reset') }),
    ).not.toBeInTheDocument()
    await user.click(
      screen.getByRole('button', { name: t('dashboard.customize.undo') }),
    )
    expect(rowNames()).toEqual([
      nameOf('hero-balance'),
      nameOf('uncategorized-banner'),
      nameOf('spending-charts'),
      nameOf('balance-evolution'),
      nameOf('period-transactions'),
      nameOf('goals-progress'),
    ])
    // Slot is one-deep: back to Reset after undo.
    expect(
      screen.getByRole('button', { name: t('dashboard.customize.reset') }),
    ).toBeInTheDocument()
  })

  it('closing the panel persists the draft once and a failed save surfaces the saveError toast', async () => {
    const { user } = await openDialog()
    const owner = h.owner(), store = owner.store, generation = owner.authGeneration
    const firstPut = h.put(firstReply), post = h.read(firstPost)

    // Successful close: exactly one write for this close.
    await user.click(
      screen.getByRole('button', {
        name: t('dashboard.customize.moveUp', {
          name: nameOf('period-transactions'),
        }),
      }),
    )
    expect(h.hook().draft).toStrictEqual(firstReply.layout)
    await user.click(screen.getByRole('button', { name: t('dashboard.customize.done') }))
    await waitFor(() => expect(mocks.saveLayout).toHaveBeenCalledTimes(1))
    expect(firstPut.issued).toBe(true); expect(h.requests).toHaveLength(1)
    h.request(0, firstReply.layout, owner)
    expect(store.getSnapshot(A)).toMatchObject({ pending: true, state: 'pending' })
    await h.settle(firstPut, post)
    expect(post.issued).toBe(true)
    expect(h.gets).toHaveLength(3); expect(h.gets[2]?.user).toBe(firstPost)
    expect(store.getSnapshot(A)).toMatchObject({ pending: false, readPending: false,
      state: 'closed-success', counters: { success: 1 } })
    expect(store.getSnapshot(A).layout).toStrictEqual(firstReply.layout)
    expect(store.getSnapshot(A).overlay).toStrictEqual({ [A]: firstReply.layout })
    expect(h.hook().customizeOpen).toBe(false)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(mocks.saveLayout).toHaveBeenCalledTimes(1)
    expect(mocks.toastError).not.toHaveBeenCalled()
    h.stable(owner); h.evidence()

    // SAME owner/client/generation/A: observe the already-issued STORE latch,
    // not merely the transport promise, and add no extra page-binding holder.
    const secondPut = h.put(firstReply), offline = new Error('offline')
    await user.click(screen.getByTestId('open-customize'))
    await user.click(
      screen.getByRole('button', {
        name: t('dashboard.customize.moveDown', {
          name: nameOf('hero-balance'),
        }),
      }),
    )
    expect(h.hook().draft).toStrictEqual(secondDraft)
    const sessionId = store.retain(A)
    expect(sessionId).not.toBeNull()
    const capturedSessionId = required(sessionId, 'second close session')
    await user.click(screen.getByRole('button', { name: t('dashboard.customize.done') }))
    await waitFor(() => expect(mocks.saveLayout).toHaveBeenCalledTimes(2))
    expect(secondPut.issued).toBe(true); expect(h.requests).toHaveLength(2)
    h.request(1, secondDraft, owner)
    expect(store.getSnapshot(A)).toMatchObject({ pending: true, state: 'pending' })
    expect(h.hook().customizeOpen).toBe(false)
    const issuedOperation = store.finalize(capturedSessionId)
    const issuedOutcome: { settled: boolean; rejected: boolean; failure: unknown } = {
      settled: false, rejected: false, failure: null,
    }
    const rejectionObserver = issuedOperation.then(() => { issuedOutcome.settled = true },
      (failure: unknown) => {
        issuedOutcome.settled = true; issuedOutcome.rejected = true; issuedOutcome.failure = failure
      })
    h.track(rejectionObserver) // Attached BEFORE rejecting the controlled PUT.
    expect(store.finalize(capturedSessionId)).toBe(issuedOperation)
    await h.flush(); expect(mocks.saveLayout).toHaveBeenCalledTimes(2)
    await act(async () => { secondPut.fail(offline); await rejectionObserver; await h.drain() })
    await h.settle()
    expect(mocks.toastError).toHaveBeenCalledTimes(1)
    expect(mocks.toastError).toHaveBeenCalledWith(
      t('dashboard.customize.saveError'),
    )
    expect(mocks.saveLayout).toHaveBeenCalledTimes(2)
    expect(mocks.updateMe).not.toHaveBeenCalled()
    expect(store.getSnapshot(A)).toMatchObject({ pending: false, readPending: false,
      writable: false, counters: { success: 1 } })
    expect(h.gets).toHaveLength(3)
    h.stable(owner); h.evidence()

    function projections(): Recovery {
      const snapshot: RecoverySnapshot = store.getSnapshot(A)
      const controller: RecoveryController = h.hook()
      // Intended first behavioral RED after the strict successful control.
      // Both projections are asserted before narrowing; no fabricated fallback.
      expect(snapshot.recovery).toBeDefined()
      expect(controller.recovery).toBeDefined()
      expect(snapshot.recovery).not.toBeNull()
      expect(controller.recovery).not.toBeNull()
      if (snapshot.recovery === undefined || snapshot.recovery === null ||
          controller.recovery === undefined || controller.recovery === null) {
        throw new Error('Recovery presence assertions must establish both real projections')
      }
      expect(controller.recovery).toStrictEqual(snapshot.recovery)
      return snapshot.recovery
    }
    const retained = projections()
    expect(retained).toStrictEqual({ attemptId: retained.attemptId, actorId: ACTOR,
      authGeneration: generation, workspaceId: A, epoch: retained.epoch,
      baseline: firstReply.layout, submitted: secondDraft,
      category: 'unknown', phase: 'needs-recheck', recheck: null })
    expect(typeof retained.attemptId).toBe('string')
    expect(retained.attemptId.trim().length).toBeGreaterThan(0)
    expect(Number.isInteger(retained.epoch)).toBe(true); expect(retained.epoch).toBeGreaterThan(0)
    for (const capture of [retained, retained.baseline, retained.submitted,
      retained.baseline.items, retained.submitted.items,
      ...retained.baseline.items, ...retained.submitted.items]) {
      expect(Object.isFrozen(capture)).toBe(true)
    }
    const retainedMetadata = structuredClone(retained)
    // Separate mandatory discriminator: current store wraps Error, unlike lease.
    // This remains an expected later RED until issued rejection is preserved.
    expect(issuedOutcome.settled).toBe(true); expect(issuedOutcome.rejected).toBe(true)
    expect(issuedOutcome.failure).toBe(offline)

    await h.show(false); await h.settle()
    expect(screen.queryByTestId('open-customize')).not.toBeInTheDocument()
    expect((store.getSnapshot(A) as RecoverySnapshot).recovery).toBe(retained)
    expect((store.getSnapshot(A) as RecoverySnapshot).recovery).toStrictEqual(retainedMetadata)
    h.stable(owner); h.evidence()
    const remountRead = h.read(firstPost), readError = new Error('remount read offline')
    await h.show(true)
    // Retained recovery never grants an automatic read on remount.
    expect(remountRead.issued).toBe(false); expect(h.gets).toHaveLength(3)
    expect((store.getSnapshot(A) as RecoverySnapshot).recovery).toBe(retained)
    expect(projections()).toStrictEqual(retainedMetadata)
    // A separate explicit user recheck owns the read and its original failure.
    const reviewedOutcome = h.hook().recheckRecovery(retained.attemptId).then(
      () => ({ rejected: false, error: null }),
      (error: unknown) => ({ rejected: true, error }),
    )
    await h.flush()
    expect(remountRead.issued).toBe(true); expect(h.gets).toHaveLength(4)
    expect(projections()).toStrictEqual({ ...retainedMetadata, phase: 'rechecking' })
    expect(store.getSnapshot(A).readPending).toBe(true)
    await act(async () => { remountRead.fail(readError); await h.drain() })
    expect(await reviewedOutcome).toMatchObject({ rejected: true, error: expect.any(Error) })
    await h.settle()
    expect(h.gets[3]).toStrictEqual({ completed: true, failure: readError, user: null })
    const afterRead = projections()
    expect(afterRead).toStrictEqual({ ...retainedMetadata, phase: 'recheck-failed' })
    expect(afterRead.baseline).toBe(retained.baseline)
    expect(afterRead.submitted).toBe(retained.submitted)
    expect(afterRead.recheck).toBeNull(); expect(Object.isFrozen(afterRead)).toBe(true)
    expect(store.getSnapshot(A)).toMatchObject({ pending: false, readPending: false,
      writable: false, counters: { success: 1 } })
    expect(store.getSnapshot(A).overlay).toStrictEqual({ [A]: firstReply.layout })
    expect(h.requests).toHaveLength(2); expect(mocks.saveLayout).toHaveBeenCalledTimes(2)
    expect(mocks.toastError).toHaveBeenCalledTimes(1)
    expect(mocks.updateMe).not.toHaveBeenCalled()
    h.stable(owner); h.evidence()
  })
})
