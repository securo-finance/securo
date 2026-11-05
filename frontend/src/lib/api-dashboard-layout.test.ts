import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  AxiosAdapter,
  AxiosInstance,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from 'axios'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { DashboardLayout } from '@/lib/dashboard-layout'

// Public contract projection only: no guessed helper exports, implementation,
// fallback, or mock saveLayout. Its absence must FAIL even negative cases.
interface DashboardRequestBinding {
  isCurrent(): boolean
}
interface CommittedDashboardLayout {
  workspaceId: string
  layout: DashboardLayout
}
interface LayoutTransport {
  saveLayout(
    workspaceId: string,
    layout: DashboardLayout,
    binding: DashboardRequestBinding,
  ): Promise<CommittedDashboardLayout>
}

type ApiModule = typeof import('@/lib/api')
type AxiosModule = typeof import('axios')
type Raw = Record<string, unknown>
type Mutation = { name: string; mutate: (layout: Raw) => void }

const TARGET = 'aabbccdd-0011-2233-4455-66778899aabb'
const OTHER = '11223344-5566-7788-99aa-bbccddeeff00'
// Deliberately not constrained to a specific UUID version or variant.
const NON_RFC_BITS = '00000000-0000-0000-0000-000000000000'
const INVALID_IDS = [
  { name: 'uppercase', value: TARGET.toUpperCase() },
  { name: 'braces', value: `{${TARGET}}` },
  { name: 'hyphenless', value: TARGET.replaceAll('-', '') },
  { name: 'malformed', value: 'ws-1' },
  { name: 'whitespace', value: ` ${TARGET}` },
]

function layout(workspaceId = TARGET, single = false): DashboardLayout {
  return {
    version: 1,
    workspaceId,
    items: (single ? DASHBOARD_WIDGETS.slice(0, 1) : DASHBOARD_WIDGETS).map(
      (widget, order) => ({ id: widget.id, visible: true, order }),
    ),
  }
}
function committed(workspaceId = TARGET): CommittedDashboardLayout {
  return { workspaceId, layout: layout(workspaceId) }
}
function rawLayout(): Raw {
  return structuredClone(layout()) as unknown as Raw
}
function items(raw: Raw): Raw[] {
  return raw.items as Raw[]
}

// Reserved values are strictly validated on BOTH sides, never passed through
// the display normalizer. Each table invocation receives a deep-fresh fixture.
const INVALID_LAYOUTS: Mutation[] = [
  { name: 'missing workspace', mutate: (raw) => { delete raw.workspaceId } },
  { name: 'wrong workspace', mutate: (raw) => { raw.workspaceId = OTHER } },
  ...INVALID_IDS.map(({ name, value }) => ({
    name: `workspace ${name}`,
    mutate: (raw: Raw) => { raw.workspaceId = value },
  })),
  { name: 'missing version', mutate: (raw) => { delete raw.version } },
  ...[0, 2, '1', true].map((version) => ({
    name: `version ${typeof version}:${String(version)}`,
    mutate: (raw: Raw) => { raw.version = version },
  })),
  { name: 'missing items', mutate: (raw) => { delete raw.items } },
  { name: 'items not an array', mutate: (raw) => { raw.items = {} } },
  { name: 'empty items', mutate: (raw) => { raw.items = [] } },
  {
    name: 'over 64 items',
    mutate: (raw) => {
      // Also duplicate IDs: six known IDs cannot form 65 unique known items.
      raw.items = Array.from({ length: 65 }, (_, order) => ({
        id: DASHBOARD_WIDGETS[order % DASHBOARD_WIDGETS.length].id,
        visible: true,
        order,
      }))
    },
  },
  { name: 'duplicate widget', mutate: (raw) => { items(raw)[1].id = items(raw)[0].id } },
  { name: 'unknown widget', mutate: (raw) => { items(raw)[0].id = 'mystery-widget' } },
  { name: 'missing widget ID', mutate: (raw) => { delete items(raw)[0].id } },
  { name: 'numeric widget ID', mutate: (raw) => { items(raw)[0].id = 42 } },
  { name: 'nonboolean visible', mutate: (raw) => { items(raw)[0].visible = 'true' } },
  { name: 'numeric visible', mutate: (raw) => { items(raw)[0].visible = 1 } },
  { name: 'missing visible', mutate: (raw) => { delete items(raw)[0].visible } },
  { name: 'fractional order', mutate: (raw) => { items(raw)[0].order = 0.5 } },
  { name: 'negative order', mutate: (raw) => { items(raw)[0].order = -1 } },
  { name: 'string order', mutate: (raw) => { items(raw)[0].order = '0' } },
  { name: 'boolean order', mutate: (raw) => { items(raw)[0].order = false } },
  { name: 'missing order', mutate: (raw) => { delete items(raw)[0].order } },
  { name: 'gapped order', mutate: (raw) => { items(raw)[5].order = 6 } },
  { name: 'duplicate order', mutate: (raw) => { items(raw)[1].order = 0 } },
  { name: 'all hidden', mutate: (raw) => { items(raw).forEach((item) => { item.visible = false }) } },
  { name: 'null item', mutate: (raw) => { raw.items = [null] } },
]

let axiosModule: AxiosModule
let apiModule: ApiModule
let client: AxiosInstance | undefined
let originalAdapter: AxiosInstance['defaults']['adapter']
let originalClientAdapter: AxiosInstance['defaults']['adapter']
let calls: InternalAxiosRequestConfig[]
let events: string[]
let interceptorDepth: number
let storage: Map<string, string>
let location: { href: string }
let responseData: unknown
let responseStatus: number
let networkFailure: boolean
let emittedError: unknown
let current: boolean
let binding: DashboardRequestBinding

function body(config: InternalAxiosRequestConfig): unknown {
  return typeof config.data === 'string' ? JSON.parse(config.data) : config.data
}
function expectOnlyPut(workspaceId = TARGET): InternalAxiosRequestConfig {
  expect(calls).toHaveLength(1)
  const config = calls[0]
  expect(config.method).toBe('put')
  expect(config.baseURL).toBe('/api')
  expect(config.url).toBe(`/dashboard/layouts/${encodeURIComponent(workspaceId)}`)
  expect(client?.getUri(config)).toBe(`/api/dashboard/layouts/${encodeURIComponent(workspaceId)}`)
  return config
}
function save(workspaceId = TARGET, value: unknown = layout()): Promise<CommittedDashboardLayout> {
  const transport = apiModule.dashboard as unknown as LayoutTransport
  // An assertion-level missing-public-method RED, not an incidental TypeError
  // accepted by a catch/rejects matcher as a negative-test PASS.
  expect(typeof transport.saveLayout, 'real dashboard.saveLayout must exist').toBe('function')
  return transport.saveLayout(workspaceId, value as DashboardLayout, binding)
}
async function rejection(operation: () => Promise<unknown>): Promise<Error> {
  // Invocation OUTSIDE catch: the public-method existence assertion cannot
  // be consumed as the expected validation error.
  const promise = operation()
  let rejected = false
  let reason: unknown
  try {
    await promise
  } catch (error) {
    rejected = true
    reason = error
  }
  expect(rejected, 'must reject rather than return normalized success').toBe(true)
  expect(reason).toBeInstanceOf(Error)
  expect(reason).not.toBeInstanceOf(TypeError)
  return reason as Error
}
function expectValidation(error: Error): void {
  // Semantic evidence without prescribing a new class or exact wording.
  expect(error.message).toMatch(/invalid|validat|malformed|workspace|uuid|layout|version|item|order|visible/i)
}

beforeEach(async () => {
  vi.resetModules()
  calls = []
  events = []
  interceptorDepth = 0
  current = true
  client = undefined
  emittedError = undefined
  responseData = committed()
  responseStatus = 200
  networkFailure = false
  storage = new Map([['token', 'synthetic-test-token'], ['workspace_id', TARGET]])
  location = { href: '/dashboard' }
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => {
      if (key === 'token') events.push('token-read')
      return storage.get(key) ?? null
    },
    setItem: (key: string, value: string) => { storage.set(key, String(value)) },
    removeItem: (key: string) => { storage.delete(key) },
    clear: () => { storage.clear() },
    key: (index: number) => Array.from(storage.keys())[index] ?? null,
    get length() { return storage.size },
  })
  vi.stubGlobal('window', { location })
  binding = {
    get isCurrent() {
      events.push(`binding-get:${interceptorDepth}`)
      return () => {
        events.push(`binding-check:${current}:${interceptorDepth}`)
        return current
      }
    },
  }

  // Dynamic imports share the same isolated axios module with REAL api.ts.
  // No application import or execution occurs until the parent runs Vitest.
  axiosModule = await import('axios')
  const axios = axiosModule.default
  originalAdapter = axios.defaults.adapter
  const adapter: AxiosAdapter = async (config) => {
    calls.push(config)
    events.push('adapter')
    const response: AxiosResponse<unknown> = {
      config,
      data: structuredClone(responseData),
      status: responseStatus,
      statusText: String(responseStatus),
      headers: {},
    }
    if (networkFailure) {
      emittedError = new axiosModule.AxiosError(
        'Synthetic network failure', axiosModule.AxiosError.ERR_NETWORK, config,
      )
      throw emittedError
    }
    // Custom adapters must implement rejection explicitly; returning a 409
    // response would otherwise bypass Axios's native-adapter HTTP semantics.
    if (responseStatus < 200 || responseStatus >= 300) {
      emittedError = new axiosModule.AxiosError(
        `Synthetic HTTP ${responseStatus}`,
        responseStatus >= 500
          ? axiosModule.AxiosError.ERR_BAD_RESPONSE
          : axiosModule.AxiosError.ERR_BAD_REQUEST,
        config, undefined, response,
      )
      throw emittedError
    }
    return response
  }
  axios.defaults.adapter = adapter
  const create = axios.create.bind(axios)
  vi.spyOn(axios, 'create').mockImplementation((config) => {
    const instance = create(config)
    client = instance
    originalClientAdapter = instance.defaults.adapter
    const use = instance.interceptors.request.use.bind(instance.interceptors.request)
    // Passthrough observation: register and invoke the ACTUAL fulfilled
    // interceptors with original rejection/options. Never fake auth logic.
    vi.spyOn(instance.interceptors.request, 'use').mockImplementation(
      (fulfilled, rejected, options) => use(fulfilled ? (request) => {
        interceptorDepth += 1
        events.push('interceptor-enter')
        request.headers = new Proxy(request.headers, {
          set(target, key, value) {
            if (String(key).toLowerCase() === 'authorization') events.push('authorization-set')
            return Reflect.set(target, key, value)
          },
        })
        try {
          return fulfilled(request)
        } finally {
          interceptorDepth -= 1
        }
      } : fulfilled, rejected, options),
    )
    return instance
  })
  apiModule = await import('@/lib/api')
  expect(client, 'capture the private real axios.create instance without exporting it').toBeDefined()
})

afterEach(() => {
  if (client) {
    client.defaults.adapter = originalClientAdapter
    client.interceptors.request.clear()
    client.interceptors.response.clear()
  }
  if (axiosModule) axiosModule.default.defaults.adapter = originalAdapter
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.resetModules()
  storage?.clear()
  calls = []
  events = []
  client = undefined
})

describe('dashboard.saveLayout real transport contract (1A)', () => {
  it.each([false, true])('accepts a canonical target and exact committed leaf (single=%s)', async (single) => {
    const draft = layout(TARGET, single)
    responseData = { workspaceId: TARGET, layout: structuredClone(draft) }
    const result = await save(TARGET, draft)
    const request = expectOnlyPut()
    expect(body(request)).toStrictEqual(draft)
    expect(result).toStrictEqual(responseData)
    expect(Object.keys(result).sort()).toEqual(['layout', 'workspaceId'])
    expect(events).toContain('interceptor-enter')
    expect(events).toContain('authorization-set')
    expect(events).toContain('adapter')
    expect(request.headers.get('Authorization') === 'Bearer synthetic-test-token').toBe(true)
    expect(request.headers.get('X-Workspace-Id')).toBe(TARGET)
  })

  it('accepts canonical UUID form without imposing UUID version/variant bits', async () => {
    storage.set('workspace_id', NON_RFC_BITS)
    responseData = committed(NON_RFC_BITS)
    expect(await save(NON_RFC_BITS, layout(NON_RFC_BITS))).toStrictEqual(responseData)
    expectOnlyPut(NON_RFC_BITS)
  })

  it('accepts an unsorted item array when its orders form the reserved set', async () => {
    const draft = layout()
    draft.items.reverse()
    responseData = { workspaceId: TARGET, layout: structuredClone(draft) }
    const result = await save(TARGET, draft)
    expectOnlyPut()
    expect([...result.layout.items].sort((a, b) => a.order - b.order)).toStrictEqual(layout().items)
    expect(result.layout.items).toHaveLength(draft.items.length)
  })

  it('writes only reserved fields, even when caller input has non-reserved extras', async () => {
    const draft = rawLayout()
    draft.preferences = { unrelated: true }
    draft.savedUser = { id: 'not-a-user-write' }
    items(draft)[0].displayLabel = 'not persisted'
    expect(await save(TARGET, draft)).toStrictEqual(committed())
    expect(body(expectOnlyPut())).toStrictEqual(layout())
    expect(draft).toHaveProperty('preferences')
    expect(items(draft)[0]).toHaveProperty('displayLabel')
  })

  it.each(INVALID_IDS)('refuses $name path UUID without repair or adapter dispatch', async ({ value }) => {
    expectValidation(await rejection(() => save(value, layout(value))))
    expect(calls).toHaveLength(0)
  })

  it.each(INVALID_LAYOUTS)('refuses malformed reserved outgoing value: $name', async ({ mutate }) => {
    const draft = rawLayout()
    mutate(draft)
    expectValidation(await rejection(() => save(TARGET, draft)))
    expect(calls).toHaveLength(0)
  })

  it('attaches the dispatch-time active header to the original path; propagates 409', async () => {
    const pending = rejection(() => save())
    storage.set('workspace_id', OTHER)
    responseStatus = 409
    const error = await pending
    expect(error).toBe(emittedError)
    expect(axiosModule.isAxiosError(error)).toBe(true)
    expect((error as InstanceType<AxiosModule['AxiosError']>).response?.status).toBe(409)
    expect(expectOnlyPut().headers.get('X-Workspace-Id')).toBe(OTHER)
  })

  it('preserves the captured path when the active header is absent', async () => {
    storage.delete('workspace_id')
    expect(await save()).toStrictEqual(committed())
    expect(expectOnlyPut().headers.has('X-Workspace-Id')).toBe(false)
  })

  it('refuses an already-revoked binding before live auth assignment and adapter dispatch', async () => {
    current = false
    const error = await rejection(() => save())
    expect(error.message).toMatch(/current|stale|revok|binding|cancel|auth|session/i)
    expect(events.some((event) => event.startsWith('binding-check:false:'))).toBe(true)
    expect(events).not.toContain('authorization-set')
    expect(events).not.toContain('token-read')
    expect(calls).toHaveLength(0)
  })

  it('rechecks binding inside the actual interceptor after call-time ownership changes', async () => {
    const pending = rejection(() => save())
    // axios's registered async request chain has not dispatched yet.
    current = false
    storage.set('token', 'synthetic-replacement-token')
    const error = await pending
    expect(error.message).toMatch(/current|stale|revok|binding|cancel|auth|session/i)
    expect(events).toContain('interceptor-enter')
    // Axios may read the getter while merging request config; the live
    // function evaluation, not getter placement, must occur at dispatch.
    expect(events.some((event) => event.startsWith('binding-get:'))).toBe(true)
    expect(events).toContain('binding-check:false:1')
    expect(events).not.toContain('token-read')
    expect(events).not.toContain('authorization-set')
    expect(calls).toHaveLength(0)
  })

  it.each(INVALID_LAYOUTS)('refuses accepted-2xx malformed committed layout: $name', async ({ mutate }) => {
    const leaf = rawLayout()
    mutate(leaf)
    responseData = { workspaceId: TARGET, layout: leaf }
    const error = await rejection(() => save())
    expectValidation(error)
    expectOnlyPut()
    // The server accepted the write; local response rejection is not success
    // and proves no generic users/me PATCH fallback was dispatched.
    expect(responseStatus).toBe(200)
  })

  it.each([
    { name: 'null envelope', make: () => null },
    { name: 'array envelope', make: () => [] },
    { name: 'missing envelope workspace', make: () => ({ layout: layout() }) },
    { name: 'wrong envelope workspace', make: () => ({ ...committed(), workspaceId: OTHER }) },
    ...INVALID_IDS.map(({ name, value }) => ({
      name: `envelope workspace ${name}`,
      make: () => ({ ...committed(), workspaceId: value }),
    })),
    { name: 'missing layout', make: () => ({ workspaceId: TARGET }) },
    { name: 'null layout', make: () => ({ workspaceId: TARGET, layout: null }) },
    { name: 'array layout', make: () => ({ workspaceId: TARGET, layout: [] }) },
    { name: 'extra envelope key', make: () => ({ ...committed(), extra: true }) },
    { name: 'full User instead of committed leaf', make: () => ({ id: 'user', preferences: {} }) },
    { name: 'extra layout key', make: () => ({ workspaceId: TARGET, layout: { ...layout(), extra: true } }) },
    {
      name: 'extra item key',
      make: () => {
        const leaf = rawLayout()
        items(leaf)[0].extra = true
        return { workspaceId: TARGET, layout: leaf }
      },
    },
  ])('rejects malformed 2xx envelope as unconfirmed outcome: $name', async ({ make }) => {
    responseData = make()
    const error = await rejection(() => save())
    expect(error.message).toMatch(/invalid|malformed|validat|response|committed|outcome|layout|workspace/i)
    expectOnlyPut()
  })

  it.each([201, 202, 204])('never treats malformed accepted HTTP %s as success', async (status) => {
    responseStatus = status
    responseData = null
    const error = await rejection(() => save())
    expect(error.message).toMatch(/invalid|malformed|validat|response|committed|outcome|layout|workspace/i)
    expectOnlyPut()
  })

  it.each([404, 403, 409, 503, 'network'] as const)('propagates %s without users/me PATCH fallback', async (failure) => {
    networkFailure = failure === 'network'
    responseStatus = typeof failure === 'number' ? failure : 200
    const error = await rejection(() => save())
    expect(error).toBe(emittedError)
    expect(axiosModule.isAxiosError(error)).toBe(true)
    const axiosError = error as InstanceType<AxiosModule['AxiosError']>
    if (failure === 'network') {
      expect(axiosError.code).toBe(axiosModule.AxiosError.ERR_NETWORK)
      expect(axiosError.response).toBeUndefined()
    } else {
      expect(axiosError.response?.status).toBe(failure)
    }
    expectOnlyPut()
  })

  it('provides a later successful request control through the identical public method and interceptor harness', async () => {
    expect(await save()).toStrictEqual(committed())
    const request = expectOnlyPut()
    expect(body(request)).toStrictEqual(layout())
    expect(events).toContain('interceptor-enter')
    expect(events).toContain('authorization-set')
    expect(events).toContain('adapter')
  })
})

describe('generic auth transport remains unchanged with the same real interceptors', () => {
  it('keeps auth.me GET and auth.updateMe PATCH authorization, workspace, body and response', async () => {
    current = false // No dashboard binding may leak into generic requests.
    responseData = { id: 'synthetic-user', preferences: { unrelated: 'preserved' } }
    expect(await apiModule.auth.me()).toStrictEqual(responseData)
    const updates = { preferences: {} }
    expect(await apiModule.auth.updateMe(updates)).toStrictEqual(responseData)
    expect(calls.map((request) => [request.method, request.url])).toEqual([
      ['get', '/users/me'], ['patch', '/users/me'],
    ])
    expect(body(calls[1])).toStrictEqual(updates)
    for (const request of calls) {
      expect(request.baseURL).toBe('/api')
      expect(request.headers.get('Authorization') === 'Bearer synthetic-test-token').toBe(true)
      expect(request.headers.get('X-Workspace-Id')).toBe(TARGET)
    }
    expect(events.filter((event) => event.startsWith('binding-'))).toEqual([])
    expect(events).toContain('authorization-set')
  })

  it.each([403, 409, 503, 'network'] as const)('preserves generic GET/PATCH failure semantics: %s', async (failure) => {
    responseStatus = typeof failure === 'number' ? failure : 200
    networkFailure = failure === 'network'
    const getError = await rejection(() => apiModule.auth.me())
    expect(getError).toBe(emittedError)
    const patchError = await rejection(() => apiModule.auth.updateMe({ preferences: {} }))
    expect(patchError).toBe(emittedError)
    expect(calls.map((request) => request.method)).toEqual(['get', 'patch'])
    expect(axiosModule.isAxiosError(getError)).toBe(true)
    expect(axiosModule.isAxiosError(patchError)).toBe(true)
    for (const error of [getError, patchError]) {
      const axiosError = error as InstanceType<AxiosModule['AxiosError']>
      if (failure === 'network') expect(axiosError.response).toBeUndefined()
      else expect(axiosError.response?.status).toBe(failure)
    }
    expect(storage.has('token')).toBe(true)
    expect(location.href).toBe('/dashboard')
  })

  it('preserves the real 401 response interceptor token removal and login redirect', async () => {
    responseStatus = 401
    const error = await rejection(() => apiModule.auth.me())
    expect(error).toBe(emittedError)
    expect(calls).toHaveLength(1)
    expect(storage.has('token')).toBe(false)
    expect(location.href).toBe('/login')
  })
})
