/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { InternalAxiosRequestConfig } from 'axios'
import { api, assets, rules } from '@/lib/api'
import { DATA_CHANGED_KEY } from '@/lib/data-changed'

// The interceptor under test only looks at method + success, so a fake
// adapter that always resolves 200 is all the transport it needs.
const okAdapter = (config: InternalAxiosRequestConfig) =>
  Promise.resolve({ data: {}, status: 200, statusText: 'OK', headers: {}, config })

describe('api data-change signal (issue #763)', () => {
  beforeEach(() => {
    localStorage.clear()
    api.defaults.adapter = okAdapter
    vi.restoreAllMocks()
  })

  it('bumps the signal after a successful mutation', async () => {
    await api.post('/transactions', { description: 'x' })
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeTruthy()
  })

  it('bumps with a new value on each mutation, so repeated changes both notify', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(1000)
    await api.patch('/transactions/1', { description: 'a' })
    const first = localStorage.getItem(DATA_CHANGED_KEY)
    await api.delete('/transactions/1')
    const second = localStorage.getItem(DATA_CHANGED_KEY)
    expect(first).toBeTruthy()
    expect(second).not.toBe(first)
  })

  it('does not bump on reads', async () => {
    await api.get('/transactions')
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeNull()
  })

  it('does not bump on a read-only POST such as the rule preview', async () => {
    await rules.preview({ conditions: [], actions: [] } as unknown as Parameters<typeof rules.preview>[0])
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeNull()
  })

  it('still bumps on a real POST mutation that sits next to the preview', async () => {
    await rules.applyAll()
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeTruthy()
  })

  it('does not bump when the mutation fails', async () => {
    api.defaults.adapter = (config: InternalAxiosRequestConfig) =>
      Promise.reject(
        Object.assign(new Error('boom'), { config, response: { status: 500 } }),
      )
    await expect(api.post('/transactions', {})).rejects.toThrow()
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeNull()
  })
})

describe('api data-change signal on a rate-limited price refresh (issue #778)', () => {
  const reject429 = (detail: unknown) => {
    api.defaults.adapter = (config: InternalAxiosRequestConfig) =>
      Promise.reject(
        Object.assign(new Error('rate limited'), {
          config,
          response: { status: 429, data: { detail } },
        }),
      )
  }

  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('bumps when the provider stopped after some prices were committed', async () => {
    // The other tabs never see that partial write otherwise: the response
    // interceptor only signals on success.
    reject429({ message: 'slow down', refreshed: 2, skipped: 1, rate_limited: 1 })
    await expect(assets.refreshPrices('ws-1')).rejects.toThrow()
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeTruthy()
  })

  it('leaves the signal alone when nothing was written before the 429', async () => {
    reject429({ message: 'slow down', refreshed: 0, skipped: 3, rate_limited: 1 })
    await expect(assets.refreshPrices('ws-1')).rejects.toThrow()
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeNull()
  })
})
