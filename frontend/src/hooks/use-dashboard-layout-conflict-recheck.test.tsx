import { expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import type { AxiosAdapter, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig, AxiosError } from 'axios'
import type { DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import type { RecoveryRead, SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import type { DashboardLayoutController } from '@/hooks/use-dashboard-layout'
import type { DialogTicket, PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import type { User, Workspace } from '@/types'
import type { ModuleId } from '@/lib/modules'

const notifications = vi.hoisted(() => ({ error: vi.fn() }))
vi.mock('sonner', () => ({ toast: { error: notifications.error } }))
// Native installed SDK/Auth/provider/store/lease/hook; only transport outcomes are substituted.
const ACTOR = '66666666-6666-4666-8666-666666666666'
const A = '77777777-7777-4777-8777-777777777777'
const TOKEN = 'synthetic-current409-positive-token'
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}
function refs(value: unknown): object[] {
  return value !== null && typeof value === 'object' ? [value, ...Object.values(value).flatMap(refs)] : []
}
function frozen(value: unknown): void {
  if (value !== null && typeof value === 'object') {
    expect(Object.isFrozen(value)).toBe(true); Object.values(value).forEach(frozen)
  }
}
function required<T>(value: T | null | undefined, label: string): T {
  if (value === null || value === undefined) throw new Error(`Missing native witness: ${label}`)
  return value
}
const P0: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const P1: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'period-transactions', visible: true, order: 4 },
  { id: 'goals-progress', visible: true, order: 5 },
] })
const P2: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'uncategorized-banner', visible: true, order: 0 },
  { id: 'hero-balance', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'period-transactions', visible: true, order: 4 },
  { id: 'goals-progress', visible: true, order: 5 },
] })
// Independently authored RAW reply literals, never derived from submitted data or a resolver.
const authUser = freeze({ id: ACTOR, email: 'auth409@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false, root_extra: { kept: ['auth'] },
  preferences: { language: 'en', caller_extra: { kept: ['auth-prefs'] }, dashboard_layouts: {
    map_extra: { kept: ['auth-map'] }, [A]: { version: 1, workspaceId: A, leaf_extra: { kept: ['auth-leaf'] }, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ] },
  } } })
const entryUser = freeze({ id: ACTOR, email: 'entry409@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false, root_extra: { kept: ['entry'] },
  preferences: { caller_extra: { kept: ['entry-prefs'] }, dashboard_layouts: {
    map_extra: { kept: ['entry-map'] }, [A]: { version: 1, workspaceId: A, leaf_extra: { kept: ['entry-leaf'] }, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ] },
  } } })
const postUser = freeze({ id: ACTOR, email: 'post409@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false, root_extra: { kept: ['post'] },
  preferences: { caller_extra: { kept: ['post-prefs'] }, dashboard_layouts: {
    map_extra: { kept: ['post-map'] }, [A]: { version: 1, workspaceId: A, leaf_extra: { kept: ['post-leaf'] }, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'period-transactions', visible: true, order: 4 },
      { id: 'goals-progress', visible: true, order: 5 },
    ] },
  } } })
const recoveryUser = freeze({ id: ACTOR, email: 'recovery409@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false, root_extra: { kept: ['recovery'] },
  preferences: { caller_extra: { kept: ['recovery-prefs'] }, dashboard_layouts: {
    map_extra: { kept: ['recovery-map'] }, [A]: { version: 1, workspaceId: A, leaf_extra: { kept: ['recovery-leaf'] }, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'period-transactions', visible: true, order: 4 },
      { id: 'goals-progress', visible: true, order: 5 },
    ] },
  } } })
const accepted = freeze({ workspaceId: A, layout: { version: 1 as const, workspaceId: A, items: [
  { id: 'hero-balance' as const, visible: true, order: 0 },
  { id: 'uncategorized-banner' as const, visible: true, order: 1 },
  { id: 'spending-charts' as const, visible: true, order: 2 },
  { id: 'balance-evolution' as const, visible: true, order: 3 },
  { id: 'period-transactions' as const, visible: true, order: 4 },
  { id: 'goals-progress' as const, visible: true, order: 5 },
] } })
const workspace: Workspace = freeze({ id: A, name: 'Synthetic current409 positive', kind: 'personal',
  timezone: null,
  is_archived: false, default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null, icon: null, color: null,
  created_at: '2026-10-06T00:00:00Z', created_by_user_id: ACTOR, managed_by_user_id: null,
  role: 'owner', enabled_modules: ['accounts', 'transactions', 'goals'] })
const conflictData = freeze({ detail: 'Synthetic current layout conflict' })
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })
type Outcome = { status: 'pending' } | { status: 'fulfilled'; value: unknown } | { status: 'rejected'; error: unknown }
function descriptorEvidence(value: object) {
  return { value, prototype: Object.getPrototypeOf(value) as unknown,
    entries: Reflect.ownKeys(value).map((key) => ({ key, descriptor: Object.getOwnPropertyDescriptor(value, key)! })) }
}
function descriptorsUnchanged(before: ReturnType<typeof descriptorEvidence>) {
  expect(Object.getPrototypeOf(before.value)).toBe(before.prototype)
  expect(Reflect.ownKeys(before.value)).toStrictEqual(before.entries.map(({ key }) => key))
  for (const { key, descriptor } of before.entries) {
    const now = required(Object.getOwnPropertyDescriptor(before.value, key), String(key))
    expect(Reflect.ownKeys(now)).toStrictEqual(Reflect.ownKeys(descriptor))
    for (const field of Reflect.ownKeys(descriptor)) expect(Reflect.get(now, field)).toBe(Reflect.get(descriptor, field))
  }
}

it('autonomously rechecks a current409 with a live native page lease and retains readonly P2 against RAW P1', async () => {
  vi.resetModules(); notifications.error.mockReset()
  const restores: Array<() => void | Promise<void>> = []
  const diagnostics: unknown[] = [], violations: unknown[] = [], asynchronous: unknown[] = []
  const observations: Array<{ label: string; expected: 'fulfilled' | '409'; task: Promise<unknown>;
    outcome: Outcome; handled: Promise<void> }> = []
  let originalError: AxiosError | null = null, nativeCleanup: (() => Promise<void>) | null = null
  let primary: unknown, failed = false
  const inputs = [P0, P1, P2, authUser, entryUser, postUser, recoveryUser, accepted, workspace, conflictData].map((value) => ({
    value, copy: structuredClone(value), identities: refs(value),
  }))
  function observe(task: Promise<unknown>, label: string, expected: 'fulfilled' | '409', boundary?: (error: unknown) => void) {
    const record: (typeof observations)[number] = { label, expected, task, outcome: { status: 'pending' }, handled: Promise.resolve() }
    record.handled = task.then((value) => { record.outcome = { status: 'fulfilled', value } }, (error: unknown) => {
      record.outcome = { status: 'rejected', error }
      try { boundary?.(error) } catch (failure: unknown) { asynchronous.push(failure) }
    })
    observations.push(record); return record
  }
  function forbid(label: string): Promise<never> {
    const error = new Error(`Forbidden current409 ${label}`); violations.push(error)
    const task = Promise.reject<never>(error); observe(task, `forbidden:${label}`, 'fulfilled'); return task
  }
  function preserveInputs() {
    for (const { value, copy, identities } of inputs) {
      expect(value).toStrictEqual(copy); frozen(value)
      const now = refs(value); expect(now).toHaveLength(identities.length)
      now.forEach((ref, index) => { expect(ref).toBe(identities[index]) })
    }
  }
  function outcomes() {
    for (const record of observations) {
      expect(record.outcome.status, record.label).toBe(record.expected === 'fulfilled' ? 'fulfilled' : 'rejected')
      if (record.outcome.status === 'rejected') expect(record.outcome.error, record.label).toBe(originalError)
    }
    expect(violations).toStrictEqual([]); expect(asynchronous).toStrictEqual([])
  }
  try {
    const axiosModule = await import('axios'), axios = axiosModule.default
    const priorAdapter = axios.defaults.adapter
    restores.push(() => { axios.defaults.adapter = priorAdapter })
    let http: AxiosInstance | null = null
    const calls: Array<{ config: InternalAxiosRequestConfig; phase: string; owner: DashboardLayoutOwner | null;
      generation: number; auth: unknown; context: unknown; start: SessionSnapshot | null; event: number }> = []
    const chronology: Array<{ kind: string; config: InternalAxiosRequestConfig }> = []
    let phase = 'auth', cleaning = false, generation: number | null = null
    let ownerWitness: DashboardLayoutOwner | null = null, authWitness: User | null = null, authContextWitness: unknown = null
    const state: { owner: DashboardLayoutOwner | null; hook: DashboardLayoutController | null;
      auth: ReturnType<typeof import('@/contexts/auth-context').useAuth> | null;
      client: unknown; workspace: Workspace | null } = { owner: null, hook: null, auth: null, client: null, workspace: null }
    let bridgeGeneration: () => number = () => 0
    function slot() {
      let resolve!: (value: AxiosResponse<unknown>) => void, reject!: (reason: unknown) => void
      const task = new Promise<AxiosResponse<unknown>>((yes, no) => { resolve = yes; reject = no })
      return { task, issued: false, settled: false, config: null as InternalAxiosRequestConfig | null,
        settle(data: unknown, failure?: unknown) {
          if (!this.issued || this.settled) return
          this.settled = true
          if (failure !== undefined) reject(failure)
          else resolve({ data, status: 200, statusText: 'OK', headers: {}, config: required(this.config, 'slot config') })
        } }
    }
    const getSlots = [slot(), slot(), slot(), slot()], putSlots = [slot(), slot()]
    const getReplies = [authUser, entryUser, postUser, recoveryUser]
    const counts = () => ({ gets: calls.filter(({ config }) => config.method === 'get').length,
      puts: calls.filter(({ config }) => config.method === 'put').length })
    const adapter: AxiosAdapter = (config) => {
      const call = { config, phase, owner: state.owner, generation: bridgeGeneration(), auth: state.auth?.user,
        context: state.auth, start: state.owner?.store.getSnapshot(A) ?? null, event: chronology.length }
      calls.push(call); chronology.push({ kind: 'adapter', config })
      const { gets, puts } = counts()
      if (config.baseURL !== '/api' || config.headers.get('Authorization') !== `Bearer ${TOKEN}` ||
        config.headers.get('X-Workspace-Id') !== A || localStorage.getItem('token') !== TOKEN ||
        localStorage.getItem('workspace_id') !== A || state.workspace !== workspace) return forbid('adapter storage/header/selection')
      if (!chronology.slice(0, call.event).some((event) => event.kind === 'interceptor-exit' && event.config === config)) return forbid('missing real interceptor')
      let port: ReturnType<typeof slot>, data: unknown
      if (config.method === 'get' && config.url === '/users/me' && config.data === undefined) {
        if (gets === 1 && puts === 0 && phase === 'auth' && state.owner === null) {
          generation = call.generation; phase = 'entry'
        } else if (gets === 2 && puts === 0 && phase === 'entry') phase = 'ready'
        else if (gets === 3 && puts === 1 && phase === 'post-save1') phase = 'after-save1'
        else if (gets === 4 && puts === 2 && phase === 'failed' && putSlots[1]!.settled) phase = 'recovery-held'
        else return forbid('GET phase/cardinality (GET5 forbidden)')
        port = getSlots[gets - 1]!; data = getReplies[gets - 1]
      } else if (config.method === 'put' && config.url === `/dashboard/layouts/${A}`) {
        const current = (config as InternalAxiosRequestConfig & { dashboardLayoutIsCurrent?: () => boolean }).dashboardLayoutIsCurrent
        if (typeof current !== 'function' || current() !== true || typeof config.data !== 'string') return forbid('PUT native binding/serialization')
        if (config.data !== JSON.stringify(puts === 1 ? P1 : P2)) return forbid('PUT exact independent body')
        if (puts === 1 && gets === 2 && phase === 'save1') phase = 'post-save1'
        else if (puts === 2 && gets === 3 && phase === 'save2') phase = 'failed'
        else return forbid('PUT phase/cardinality (PUT3 forbidden)')
        port = putSlots[puts - 1]!; data = accepted
        if (puts === 2) {
          const response: AxiosResponse<unknown> = { status: 409, statusText: 'Conflict', data: conflictData,
            headers: { 'x-synthetic-current409': 'owned' }, config }
          originalError = new axiosModule.AxiosError('Synthetic current HTTP409', axiosModule.AxiosError.ERR_BAD_REQUEST,
            config, { syntheticRequest: 'put2' }, response)
        }
      } else return forbid(`method/URL ${config.method}:${config.url}`)
      if (call.generation !== generation) return forbid('generation')
      if (gets !== 1 || config.method !== 'get') {
        if (!ownerWitness || call.owner !== ownerWitness || ownerWitness.actorId !== ACTOR ||
          ownerWitness.authGeneration !== call.generation || call.auth !== authWitness || call.context !== authContextWitness) return forbid('owner/Auth identity')
      }
      port.config = config; port.issued = true
      observe(port.task, `adapter:${config.method}:${config.method === 'get' ? gets : puts}`,
        config.method === 'put' && puts === 2 ? '409' : 'fulfilled')
      if (cleaning) port.settle(data, config.method === 'put' && puts === 2 ? required(originalError, 'cleanup original409') : undefined)
      return port.task
    }
    axios.defaults.adapter = adapter
    const create = axios.create.bind(axios)
    const createSpy = vi.spyOn(axios, 'create').mockImplementation((config) => {
      const instance = create(config); http = instance
      const clientAdapter = instance.defaults.adapter
      restores.push(() => { instance.defaults.adapter = clientAdapter })
      restores.push(() => { instance.interceptors.request.clear() })
      restores.push(() => { instance.interceptors.response.clear() })
      const registerInterceptor = instance.interceptors.request.use.bind(instance.interceptors.request)
      const interceptorSpy = vi.spyOn(instance.interceptors.request, 'use').mockImplementation((fulfilled, rejected, options) =>
        registerInterceptor(fulfilled ? (request) => {
          chronology.push({ kind: 'interceptor-enter', config: request })
          try { return fulfilled(request) }
          finally { chronology.push({ kind: 'interceptor-exit', config: request }) }
        } : fulfilled, rejected, options))
      restores.push(() => { interceptorSpy.mockRestore() }); return instance
    })
    restores.push(() => { createSpy.mockRestore() })
    const { useLayoutEffect } = await import('react')
    const { act, render } = await import('@testing-library/react/pure')
    const { QueryClient, QueryClientProvider, useQueryClient } = await import('@tanstack/react-query')
    const { I18nextProvider } = await import('react-i18next')
    const api = await import('@/lib/api')
    const { AuthProvider } = await import('@/contexts/auth-provider')
    const { useAuth } = await import('@/contexts/auth-context')
    const { WorkspaceContext, useWorkspace } = await import('@/contexts/workspace-context')
    const { DashboardLayoutProvider } = await import('@/contexts/dashboard-layout-provider')
    const { useDashboardLayoutOwner } = await import('@/contexts/dashboard-layout-context')
    const { useDashboardLayout } = await import('@/hooks/use-dashboard-layout')
    const { getPageSessionLease } = await import('@/lib/dashboard-layout-page-session-lease')
    const { getDashboardAuthSnapshot } = await import('@/lib/dashboard-layout-auth-bridge')
    const { validateDashboardLayoutRequest, validateCommittedDashboardLayout } = await import('@/lib/dashboard-layout')
    const { DASHBOARD_WIDGETS } = await import('@/components/dashboard/widgets')
    const { default: i18next } = await import('i18next')
    const { default: english } = await import('@/locales/en.json')
    const { default: portuguese } = await import('@/locales/pt-BR.json')
    const i18n = i18next.createInstance()
    await i18n.init({ lng: 'en', fallbackLng: 'en', initAsync: false,
      resources: { en: { translation: english }, 'pt-BR': { translation: portuguese } }, interpolation: { escapeValue: false } })
    restores.push(() => { i18n.off('languageChanged loaded initialized failedLoading') })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 }, mutations: { retry: false } } })
    restores.push(() => { client.clear() })
    bridgeGeneration = () => getDashboardAuthSnapshot(client).generation
    expect(createSpy).toHaveBeenCalledTimes(1)
    expect(required<AxiosInstance>(http, 'real axios client').defaults.adapter).toBe(adapter)
    const apiFailures: Array<ReturnType<typeof descriptorEvidence>> = []
    let errorData: unknown, errorHeaders: unknown, errorConfig: unknown, errorRequest: unknown, errorHeaderValues: unknown
    const sdkCalls: Array<{ id: string; input: DashboardLayout; copy: DashboardLayout; identities: object[];
      binding: DashboardRequestBinding; task: Promise<unknown> }> = []
    const save = api.dashboard.saveLayout.bind(api.dashboard)
    const saveSpy = vi.spyOn(api.dashboard, 'saveLayout').mockImplementation((id, layout, binding) => {
      const task = save(id, layout, binding)
      sdkCalls.push({ id, input: layout, copy: structuredClone(layout), identities: refs(layout), binding, task })
      observe(task, `sdk:save${sdkCalls.length}`, sdkCalls.length === 2 ? '409' : 'fulfilled', (error) => {
        // Baseline AFTER Axios transforms, BEFORE the store's await resumes; no SDK object is prefrozen.
        if (error === originalError && originalError) {
          apiFailures.push(descriptorEvidence(originalError))
          if (originalError.response) apiFailures.push(descriptorEvidence(originalError.response))
          if (originalError.config) apiFailures.push(descriptorEvidence(originalError.config))
          errorData = originalError.response?.data; errorHeaders = originalError.response?.headers
          errorConfig = originalError.config; errorRequest = originalError.request
          if (originalError.response?.headers instanceof axiosModule.AxiosHeaders) {
            errorHeaderValues = Object.entries(originalError.response.headers.toJSON())
            apiFailures.push(descriptorEvidence(originalError.response.headers))
          }
        }
      }); return task
    })
    restores.push(() => { saveSpy.mockRestore() })
    const me = api.auth.me.bind(api.auth)
    const meSpy = vi.spyOn(api.auth, 'me').mockImplementation(() => {
      const task = me(); observe(task, 'sdk:me', 'fulfilled'); return task
    })
    restores.push(() => { meSpy.mockRestore() })
    const legacySpy = vi.spyOn(api.auth, 'updateMe')
    restores.push(() => { legacySpy.mockRestore() })
    const owners = new Set<DashboardLayoutOwner>(), contexts = new WeakSet<object>()
    const publications: SessionSnapshot[] = [], publicReads: RecoveryRead[] = [], unsubscribes: Array<() => void> = []
    const bound: PageBinding[] = [], released: PageBinding[] = [], tickets: DialogTicket[] = []
    const closeCalls: Array<{ binding: PageBinding; ticket: DialogTicket; task: Promise<void> }> = []
    const recoveryCalls: Array<{ attemptId: string; task: Promise<void>; start: SessionSnapshot }> = []
    const bindingReads: Array<{ binding: PageBinding; task: Promise<void> }> = []
    let mounts = 0, unmounts = 0, mounted: { unmount(): void } | null = null
    function ObserveAuth({ children }: { children: ReactNode }) {
      const context = useAuth()
      if (!contexts.has(context)) {
        contexts.add(context)
        for (const key of ['updateUser', 'login', 'verify2fa', 'loginWithToken', 'register', 'logout'] as const) {
          const spy = vi.spyOn(context, key).mockImplementation(() => {
            const error = new Error(`Forbidden Auth ${key}`); violations.push(error); throw error
          })
          restores.push(() => { spy.mockRestore() })
        }
      }
      useLayoutEffect(() => { state.auth = context }); return children
    }
    function Probe() {
      const controller = useDashboardLayout(), currentOwner = useDashboardLayoutOwner(), observedClient = useQueryClient()
      const selectedWorkspace = useWorkspace().current
      useLayoutEffect(() => {
        state.hook = controller; state.owner = currentOwner; state.client = observedClient; state.workspace = selectedWorkspace
        if (currentOwner && !owners.has(currentOwner)) {
          owners.add(currentOwner); ownerWitness = currentOwner
          authWitness = required(state.auth?.user, 'restored Auth user'); authContextWitness = state.auth
          unsubscribes.push(currentOwner.store.subscribe(() => {
            try {
              const snapshot = currentOwner.store.getSnapshot(A); publications.push(snapshot)
              if (snapshot.recovery?.recheck) publicReads.push(snapshot.recovery.recheck)
            } catch (error: unknown) { asynchronous.push(error) }
          }))
          const finalize = currentOwner.store.finalize.bind(currentOwner.store)
          const finalizeSpy = vi.spyOn(currentOwner.store, 'finalize').mockImplementation((...args: Parameters<typeof finalize>) => {
            // Preserve ALL native arguments, including the prospective optional lease witness.
            const task = finalize(...args)
            observe(task, `store:${args[0]}`, args[0] === 'dashboard-session-2' ? '409' : 'fulfilled'); return task
          })
          restores.push(() => { finalizeSpy.mockRestore() })
          const recheck = currentOwner.store.recheckRecovery.bind(currentOwner.store)
          const recheckSpy = vi.spyOn(currentOwner.store, 'recheckRecovery').mockImplementation((...args: Parameters<typeof recheck>) => {
            const task = recheck(...args)
            recoveryCalls.push({ attemptId: args[1], task, start: currentOwner.store.getSnapshot(A) })
            observe(task, 'store:recovery', 'fulfilled'); return task
          })
          restores.push(() => { recheckSpy.mockRestore() })
          const lease = getPageSessionLease(currentOwner.store), bind = lease.bind.bind(lease)
          const bindSpy = vi.spyOn(lease, 'bind').mockImplementation((id) => {
            const binding = bind(id); bound.push(binding)
            const open = binding.open.bind(binding), close = binding.close.bind(binding)
            const read = binding.recheckRecovery.bind(binding), release = binding.release.bind(binding)
            const openSpy = vi.spyOn(binding, 'open').mockImplementation(() => { const ticket = open(); tickets.push(ticket); return ticket })
            const closeSpy = vi.spyOn(binding, 'close').mockImplementation((ticket, persist) => {
              const task = close(ticket, persist); closeCalls.push({ binding, ticket, task })
              observe(task, `lease:${ticket.sessionId}`, ticket.sessionId === 'dashboard-session-2' ? '409' : 'fulfilled'); return task
            })
            const readSpy = vi.spyOn(binding, 'recheckRecovery').mockImplementation((attemptId) => {
              const task = read(attemptId); bindingReads.push({ binding, task }); observe(task, 'lease:recovery', 'fulfilled'); return task
            })
            const releaseSpy = vi.spyOn(binding, 'release').mockImplementation(() => { released.push(binding); return release() })
            for (const spy of [openSpy, closeSpy, readSpy, releaseSpy]) restores.push(() => { spy.mockRestore() })
            return binding
          })
          restores.push(() => { bindSpy.mockRestore() })
        }
      })
      useLayoutEffect(() => { mounts += 1; return () => { unmounts += 1; state.hook = null } }, [])
      return null
    }
    const seam = { current: workspace, workspaces: [workspace], isLoading: false, role: workspace.role,
      canWrite: true, canManage: true, enabledModules: ['accounts', 'transactions', 'goals'] as ModuleId[],
      hasModule: (id: typeof workspace.enabled_modules[number]) => workspace.enabled_modules.includes(id),
      switchWorkspace: () => forbid('workspace switch'), refresh: () => forbid('workspace refresh') }
    function Shell() {
      return <I18nextProvider i18n={i18n}><QueryClientProvider client={client}><AuthProvider><ObserveAuth>
        <WorkspaceContext.Provider value={seam}><DashboardLayoutProvider><Probe /></DashboardLayoutProvider></WorkspaceContext.Provider>
      </ObserveAuth></AuthProvider></QueryClientProvider></I18nextProvider>
    }
    const hook = () => required(state.hook, 'mounted hook')
    const owner = () => required(state.owner, 'mounted owner')
    async function flush() { await act(async () => { await tick(); await tick() }) }
    async function drain() {
      for (let turn = 0; turn < 12; turn += 1) {
        const before = [observations.length, publications.length, calls.length, owners.size, mounts, unmounts]
        await tick(); await tick()
        await Promise.all(observations.map(({ handled }) => handled))
        for (const current of owners) { await current.store.whenIdle(); await getPageSessionLease(current.store).whenIdle() }
        await tick(); await tick()
        const after = [observations.length, publications.length, calls.length, owners.size, mounts, unmounts]
        if (after.every((value, index) => value === before[index])) { expect(client.isMutating()).toBe(0); return }
      }
      throw new Error('Current409 native owned-task/publication fixed point not reached')
    }
    function stable() {
      expect(state.owner).toBe(ownerWitness); expect([...owners]).toStrictEqual([ownerWitness])
      expect(owner().actorId).toBe(ACTOR); expect(owner().authGeneration).toBe(generation)
      expect(bridgeGeneration()).toBe(generation); expect(state.auth).toBe(authContextWitness)
      expect(state.auth?.user).toBe(authWitness); expect(authWitness).toBe(authUser)
      expect(state.auth?.token).toBe(TOKEN); expect(state.auth?.isLoading).toBe(false)
      expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(api.WORKSPACE_STORAGE_KEY)).toBe(A)
      expect(state.client).toBe(client); expect(state.workspace).toBe(workspace)
      expect(mounts).toBe(1); expect(unmounts).toBe(0); expect(bound).toHaveLength(1); expect(released).toStrictEqual([])
    }
    function evidence(gets: number, puts: number, toast: number) {
      expect(counts()).toStrictEqual({ gets, puts }); expect(meSpy).toHaveBeenCalledTimes(gets)
      expect(saveSpy).toHaveBeenCalledTimes(puts); expect(legacySpy).not.toHaveBeenCalled()
      expect(notifications.error.mock.calls).toStrictEqual(toast ? [[i18n.t('dashboard.customize.saveError')]] : [])
      expect(violations).toStrictEqual([]); expect(asynchronous).toStrictEqual([]); preserveInputs()
      sdkCalls.forEach((call, index) => {
        expect(call.id).toBe(A); expect(call.input).toStrictEqual(index === 0 ? P1 : P2); expect(call.input).toStrictEqual(call.copy)
        const now = refs(call.input); expect(now).toHaveLength(call.identities.length)
        now.forEach((ref, i) => { expect(ref).toBe(call.identities[i]) })
        expect(validateDashboardLayoutRequest(A, call.input, DASHBOARD_WIDGETS)).toStrictEqual(index === 0 ? P1 : P2)
      })
    }
    function errorPreserved() {
      if (!putSlots[1]!.settled) return
      const error = required(originalError, 'installed AxiosError409')
      expect(error).toBeInstanceOf(axiosModule.AxiosError); expect(error).toBeInstanceOf(Error)
      expect(Object.getPrototypeOf(error)).not.toBe(Object.prototype)
      expect(error.code).toBe(axiosModule.AxiosError.ERR_BAD_REQUEST); expect(error.response?.status).toBe(409)
      expect(apiFailures).toHaveLength(4); apiFailures.forEach(descriptorsUnchanged)
      expect(error.config).toBe(errorConfig); expect(error.request).toBe(errorRequest)
      expect(error.response?.data).toBe(errorData); expect(error.response?.data).toStrictEqual(conflictData)
      expect(error.response?.headers).toBe(errorHeaders); expect(error.response?.headers).toBeInstanceOf(axiosModule.AxiosHeaders)
      if (error.response?.headers instanceof axiosModule.AxiosHeaders) {
        expect(Object.entries(error.response.headers.toJSON())).toStrictEqual(errorHeaderValues)
        expect(error.response.headers.get('x-synthetic-current409')).toBe('owned')
      }
      expect(error.request).toStrictEqual({ syntheticRequest: 'put2' }); expect(error.message).toBe('Synthetic current HTTP409')
      expect(error.config).toBe(putSlots[1]!.config); expect(error.config?.data).toBe(JSON.stringify(P2))
      expect(error.config?.headers.get('Authorization')).toBe(`Bearer ${TOKEN}`); expect(error.config?.headers.get('X-Workspace-Id')).toBe(A)
    }
    const savedStorage = ['token', api.WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
    for (const { key, value } of savedStorage) restores.push(() => {
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value)
    })
    const onError = (event: ErrorEvent) => { asynchronous.push(event.error ?? event.message) }
    const onRejection = (event: PromiseRejectionEvent) => { asynchronous.push(event.reason) }
    window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
    restores.push(() => { window.removeEventListener('error', onError) })
    restores.push(() => { window.removeEventListener('unhandledrejection', onRejection) })
    nativeCleanup = async () => {
      async function step(action: () => void | Promise<void>) {
        try { await action() } catch (error: unknown) { diagnostics.push(error) }
      }
      cleaning = true
      // Admit/settle lawful late work, but never demand or manually trigger GET4 on pre-fix RED.
      await step(flush)
      await step(async () => { await act(async () => {
        getSlots.forEach((port, i) => { port.settle(getReplies[i]) })
        putSlots[0]!.settle(accepted)
        if (putSlots[1]!.issued) putSlots[1]!.settle(undefined, required(originalError, 'cleanup409'))
        await drain()
      }) })
      await step(flush)
      const beforeUnmount = counts()
      await step(async () => { await act(async () => { mounted?.unmount(); await drain() }) })
      await step(flush)
      await step(() => {
        expect(counts()).toStrictEqual(beforeUnmount)
        for (const current of owners) {
          const snapshot = current.store.getSnapshot(A)
          expect(snapshot).toStrictEqual({ actorId: ACTOR, authGeneration: current.authGeneration, workspaceId: A,
            provenance: 'default', writable: false, state: 'closed', pending: false, readPending: false,
            canUndo: false, layout: P0, openedBaseline: null, draft: null, recovery: null, overlay: {}, counters: snapshot.counters })
          frozen(snapshot); expect(current.store.retain(A)).toBeNull()
        }
        expect(unmounts).toBe(mounts); expect(released).toStrictEqual(bound)
        sdkCalls.forEach(({ binding }) => { expect(binding.isCurrent()).toBe(false) })
      })
      await step(() => { outcomes(); errorPreserved(); preserveInputs() })
      await step(() => {
        [...getSlots, ...putSlots].filter(({ issued }) => issued).forEach(({ settled }) => { expect(settled).toBe(true) })
        expect(legacySpy).not.toHaveBeenCalled()
        if (putSlots[1]!.issued) expect(counts()).toStrictEqual({ gets: getSlots[3]!.issued ? 4 : 3, puts: 2 })
        expect(notifications.error.mock.calls).toStrictEqual(putSlots[1]!.settled ? [[i18n.t('dashboard.customize.saveError')]] : [])
      })
      for (const unsubscribe of unsubscribes) await step(unsubscribe)
    }
    localStorage.setItem('token', TOKEN); localStorage.setItem(api.WORKSPACE_STORAGE_KEY, A)
    mounted = render(<Shell />, { reactStrictMode: false })
    await flush(); expect(getSlots[0]!.issued).toBe(true)
    await act(async () => { getSlots[0]!.settle(authUser); await tick(); await tick() }); await flush(); await flush()
    expect(getSlots[1]!.issued).toBe(true)
    await act(async () => { getSlots[1]!.settle(entryUser); await drain() }); await flush()
    const current = owner(), store = current.store
    stable(); evidence(2, 0, 0)
    for (const [user, golden] of [[authUser, P0], [entryUser, P0], [postUser, P1], [recoveryUser, P1]] as const) {
      const preferences = user.preferences, map = preferences.dashboard_layouts, leaf = map[A]
      for (const value of [user, preferences, map, leaf, ...leaf.items]) expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
      expect(Object.hasOwn(user, 'id')).toBe(true); expect(Object.hasOwn(user, 'preferences')).toBe(true)
      expect(Object.hasOwn(preferences, 'dashboard_layouts')).toBe(true); expect(Object.hasOwn(map, A)).toBe(true)
      expect(validateDashboardLayoutRequest(A, leaf, DASHBOARD_WIDGETS)).toStrictEqual(golden)
    }
    expect(validateCommittedDashboardLayout(A, accepted, DASHBOARD_WIDGETS)).toStrictEqual(accepted)
    expect(store.getSnapshot(A)).toStrictEqual({ actorId: ACTOR, authGeneration: current.authGeneration, workspaceId: A,
      provenance: 'saved', writable: true, state: 'closed', pending: false, readPending: false, canUndo: false,
      layout: P0, openedBaseline: null, draft: null, recovery: null, overlay: {},
      counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
    act(() => { hook().openCustomize() }); act(() => { hook().move('period-transactions', 'up') })
    expect(hook().draft).toStrictEqual(P1); expect(tickets[0]?.sessionId).toBe('dashboard-session-1')
    phase = 'save1'; act(() => { hook().closeCustomize(true) })
    const successTask = store.finalize(required(tickets[0], 'SAVE1 ticket').sessionId)
    expect(store.finalize(tickets[0]!.sessionId)).toBe(successTask)
    await flush(); expect(putSlots[0]!.issued).toBe(true); evidence(2, 1, 0)
    expect(sdkCalls[0]!.binding.isCurrent()).toBe(true)
    await act(async () => { putSlots[0]!.settle(accepted); await tick(); await tick() }); await flush()
    expect(getSlots[2]!.issued).toBe(true)
    await act(async () => { getSlots[2]!.settle(postUser); await drain() }); await flush()
    expect(store.getSnapshot(A)).toStrictEqual({ actorId: ACTOR, authGeneration: current.authGeneration, workspaceId: A,
      provenance: 'saved', writable: true, state: 'closed-success', pending: false, readPending: false, canUndo: false,
      layout: P1, openedBaseline: null, draft: null, recovery: null, overlay: { [A]: P1 },
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
    expect(sdkCalls[0]!.binding.isCurrent()).toBe(false) // Consumed success is not page authority.
    expect(hook().customizeOpen).toBe(false); expect(hook().draft).toStrictEqual(P1); expect(hook().recovery).toBeNull()
    stable(); outcomes(); evidence(3, 1, 0)
    const bridgeBefore = getDashboardAuthSnapshot(client), bridgeCopy = structuredClone(bridgeBefore)
    act(() => { hook().openCustomize() }); act(() => { hook().move('hero-balance', 'down') })
    const ticket = required(tickets[1], 'native SAVE2 ticket')
    expect(ticket).toStrictEqual({ sessionId: 'dashboard-session-2', workspaceId: A }); frozen(ticket)
    expect(store.retain(A)).toBe(ticket.sessionId); expect(hook().draft).toStrictEqual(P2)
    const editing = store.getSnapshot(A), editingCopy = structuredClone(editing), editingRefs = refs(editing)
    phase = 'save2'; act(() => { hook().closeCustomize(true) })
    const storeTask = store.finalize(ticket.sessionId), storeDuplicate = store.finalize(ticket.sessionId)
    expect(storeDuplicate).toBe(storeTask)
    const leaseCall = required(closeCalls.find((call) => call.ticket === ticket), 'native hook lease close')
    const leaseTask = leaseCall.task, leaseDuplicate = leaseCall.binding.close(ticket, true)
    expect(leaseDuplicate).toBe(leaseTask) // Per-layer equality only.
    await flush(); expect(putSlots[1]!.issued).toBe(true); expect(putSlots[1]!.settled).toBe(false)
    const pending = store.getSnapshot(A)
    expect(pending.state).toBe('pending'); expect(pending.pending).toBe(true); expect(pending.readPending).toBe(false)
    expect(pending.recovery).toBeNull(); expect(pending.openedBaseline).toStrictEqual(P1); expect(pending.draft).toStrictEqual(P2); frozen(pending)
    expect(sdkCalls[1]!.binding.isCurrent()).toBe(true)
    const config = required(putSlots[1]!.config, 'real adapter PUT2 config')
    const marker = (config as InternalAxiosRequestConfig & { dashboardLayoutIsCurrent?: () => boolean }).dashboardLayoutIsCurrent
    expect(typeof marker).toBe('function'); expect(marker?.()).toBe(true)
    expect(config.method).toBe('put'); expect(config.url).toBe(`/dashboard/layouts/${A}`)
    expect(required<AxiosInstance>(http, 'client').getUri(config)).toBe(`/api/dashboard/layouts/${A}`)
    expect(config.data).toBe(JSON.stringify(P2)); expect(config.headers.get('Authorization')).toBe(`Bearer ${TOKEN}`)
    expect(config.headers.get('X-Workspace-Id')).toBe(A)
    expect(chronology.filter(({ config: seen, kind }) => seen === config && kind === 'adapter')).toHaveLength(1)
    expect(apiFailures).toStrictEqual([]); evidence(3, 2, 0); stable()
    await flush(); expect(marker?.()).toBe(true); expect(sdkCalls[1]!.binding.isCurrent()).toBe(true)
    await act(async () => { putSlots[1]!.settle(undefined, required(originalError, 'original409')); await tick(); await tick() })
    // Bounded native microtask/React publication barrier, NOT whenIdle or a held-read drain.
    for (let turn = 0; turn < 16; turn += 1) await flush()
    const recovery = required(store.getSnapshot(A).recovery, 'actual retained failed attempt')
    // FIRST intended missing-behavior assertion, after authentic current issuance and real SDK rejection.
    expect(recovery.category).toBe('conflict')
    expect(getSlots[3]!.issued, 'autonomous GET4 without a manual recheck').toBe(true)
    expect(getSlots[3]!.settled).toBe(false)
    const writeObservations = observations.filter(({ task }) => task === storeTask || task === leaseTask || task === sdkCalls[1]!.task)
    expect(writeObservations.length).toBeGreaterThanOrEqual(3)
    for (const record of writeObservations) {
      expect(record.outcome.status, `${record.label} settles while GET4 held`).toBe('rejected')
      if (record.outcome.status === 'rejected') expect(record.outcome.error).toBe(originalError)
    }
    errorPreserved(); stable(); evidence(4, 2, 1)
    expect(store.finalize(ticket.sessionId)).toBe(storeTask); expect(leaseCall.binding.close(ticket, true)).toBe(leaseTask)
    expect(store.retain(A)).toBeNull(); expect(bound).toStrictEqual([leaseCall.binding]); expect(released).toStrictEqual([])
    expect(recovery).toStrictEqual({ attemptId: 'dashboard-attempt-2', actorId: ACTOR,
      authGeneration: current.authGeneration, workspaceId: A, epoch: 2, baseline: P1, submitted: P2,
      category: 'conflict', phase: 'rechecking', recheck: null }); frozen(recovery)
    const baseline = recovery.baseline, submitted = recovery.submitted
    const retainedCopy = structuredClone(recovery), retainedRefs = refs(recovery)
    const held = store.getSnapshot(A), heldCopy = structuredClone(held)
    expect(held).toStrictEqual({ actorId: ACTOR, authGeneration: current.authGeneration, workspaceId: A,
      provenance: 'saved', writable: false, state: 'conflict', pending: false, readPending: true, canUndo: false,
      layout: P1, openedBaseline: P1, draft: P2, recovery, overlay: { [A]: P1 },
      counters: { success: 1, suppressed: 0, conflict409: 1, other4xx: 0, server5xx: 0 } })
    expect(hook().recovery).toBe(recovery); expect(hook().draft).toStrictEqual(P1)
    expect(hook().orderedVisible.map(({ id }) => id)).toStrictEqual(P1.items.map(({ id }) => id))
    expect(hook().customizeOpen).toBe(false); expect(hook().canUndo).toBe(false)
    expect(editing).toStrictEqual(editingCopy); refs(editing).forEach((ref, index) => { expect(ref).toBe(editingRefs[index]) }); frozen(editing)
    expect(recovery.submitted).not.toBe(sdkCalls[1]!.input); expect(recovery.submitted.items).not.toBe(sdkCalls[1]!.input.items)
    const autonomous = required(recoveryCalls[0], 'observed native autonomous recovery task')
    expect(recoveryCalls).toHaveLength(1); expect(bindingReads).toStrictEqual([])
    expect(autonomous.attemptId).toBe(recovery.attemptId); expect(autonomous.start.recovery).toBe(recovery)
    const readTask = autonomous.task
    expect(observations.find(({ task }) => task === readTask)?.outcome.status).toBe('pending')
    // Manual forwarders are used ONLY after autonomous GET4 is already held.
    const manualStore = store.recheckRecovery(A, recovery.attemptId)
    const manualHook = hook().recheckRecovery(recovery.attemptId)
    observe(manualStore, 'manual:store', 'fulfilled'); observe(manualHook, 'manual:hook', 'fulfilled')
    expect(manualStore).toBe(readTask); expect(manualHook).toBe(readTask)
    expect(bindingReads).toStrictEqual([{ binding: leaseCall.binding, task: readTask }])
    expect(recoveryCalls).toHaveLength(3); recoveryCalls.forEach(({ task }) => { expect(task).toBe(readTask) })
    await flush(); expect(store.getSnapshot(A)).toBe(held); evidence(4, 2, 1); stable()
    expect(getSlots[3]!.settled).toBe(false); expect(held).toStrictEqual(heldCopy)
    await act(async () => { getSlots[3]!.settle(recoveryUser); await drain() }); await flush()
    const ready = store.getSnapshot(A), reviewed = required(ready.recovery, 'public review-ready recovery')
    const read = required(reviewed.recheck, 'public authoritative RecoveryRead')
    const publishedRead = required(publicReads[0], 'observed published recovery read')
    // Observe the native public readId; never fabricate an expected private serial/permission.
    expect(Number.isSafeInteger(publishedRead.readId)).toBe(true); expect(publishedRead.readId).toBeGreaterThan(0)
    expect(read).toBe(publishedRead); expect(read.readId).toBe(publishedRead.readId)
    publicReads.forEach((observed) => { expect(observed).toBe(read); expect(observed.readId).toBe(read.readId) })
    expect(read).toStrictEqual({ readId: publishedRead.readId, provenance: 'saved', target: P1, matchesSubmitted: false }); frozen(read)
    expect(read.target).not.toBe(submitted); expect(read.target).not.toStrictEqual(P2)
    expect(reviewed).toStrictEqual({ attemptId: recovery.attemptId, actorId: ACTOR,
      authGeneration: current.authGeneration, workspaceId: A, epoch: 2, baseline: P1, submitted: P2,
      category: 'conflict', phase: 'review-ready', recheck: read }); frozen(reviewed)
    expect(reviewed.baseline).toBe(baseline); expect(reviewed.submitted).toBe(submitted)
    expect(recovery).toStrictEqual(retainedCopy); refs(recovery).forEach((ref, index) => { expect(ref).toBe(retainedRefs[index]) }); frozen(recovery)
    expect(ready).toStrictEqual({ actorId: ACTOR, authGeneration: current.authGeneration, workspaceId: A,
      provenance: 'saved', writable: false, state: 'conflict', pending: false, readPending: false, canUndo: false,
      layout: P1, openedBaseline: P1, draft: P2, recovery: reviewed, overlay: { [A]: P1 },
      counters: { success: 1, suppressed: 0, conflict409: 1, other4xx: 0, server5xx: 0 } }); frozen(ready)
    expect(hook().recovery).toBe(reviewed); expect(hook().draft).toStrictEqual(P1)
    expect(hook().orderedVisible.map(({ id }) => id)).toStrictEqual(P1.items.map(({ id }) => id))
    expect(() => store.open(A)).toThrow('Dashboard layout session cannot open')
    expect(() => leaseCall.binding.open()).toThrow('Dashboard layout session cannot open')
    expect(() => store.setDraft(ticket.sessionId, P0.items)).toThrow('Dashboard layout session is not editable')
    expect(() => store.reset(ticket.sessionId)).toThrow('Dashboard layout session is not editable')
    expect(() => store.undoReset(ticket.sessionId)).toThrow('Dashboard layout session is not editable')
    act(() => { hook().openCustomize(); hook().move('hero-balance', 'up'); hook().reorder(0, 1)
      expect(hook().toggleVisible('goals-progress')).toBe(false)
      hook().reset(); hook().undoReset(); hook().closeCustomize(true); store.discard(ticket.sessionId)
    })
    await act(async () => { await drain() }); await flush()
    expect(store.getSnapshot(A)).toBe(ready); expect(store.retain(A)).toBeNull()
    expect(store.finalize(ticket.sessionId)).toBe(storeTask); expect(leaseCall.binding.close(ticket, false)).toBe(leaseTask)
    await flush()
    expect(getDashboardAuthSnapshot(client)).toBe(bridgeBefore); expect(bridgeBefore).toStrictEqual(bridgeCopy); frozen(bridgeBefore)
    expect(calls.map(({ phase: started }) => started)).toStrictEqual(['auth', 'entry', 'save1', 'post-save1', 'save2', 'failed'])
    expect(calls.map(({ config: request }) => request.method)).toStrictEqual(['get', 'get', 'put', 'get', 'put', 'get'])
    expect(calls[5]!.start?.recovery).toBe(recovery); expect(calls[5]!.start?.readPending).toBe(true)
    expect(tickets).toHaveLength(2); outcomes(); errorPreserved(); stable(); evidence(4, 2, 1)
  } catch (error: unknown) { primary = error; failed = true }
  finally {
    try { await nativeCleanup?.() } catch (error: unknown) { diagnostics.push(error) }
    for (const restore of restores) { try { await restore() } catch (error: unknown) { diagnostics.push(error) } }
    try { notifications.error.mockReset(); vi.resetModules() } catch (error: unknown) { diagnostics.push(error) }
  }
  if (failed) {
    if (diagnostics.length) console.error('Current409 cleanup diagnostics; primary assertion preserved', diagnostics)
    throw primary
  }
  if (diagnostics.length) throw new AggregateError(diagnostics, 'Current409 cleanup failed')
})
