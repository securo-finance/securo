/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { InternalAxiosRequestConfig } from 'axios'
import { api, assets, rules, transactions } from '@/lib/api'
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
    const detail = { message: 'slow down', refreshed: 2, skipped: 1, rate_limited: 1 }
    reject429(detail)
    // The caller still gets the real 429 and its counters: the page reads
    // `detail.refreshed` to refetch and to say how many prices came in.
    await expect(assets.refreshPrices('ws-1')).rejects.toMatchObject({
      response: { status: 429, data: { detail } },
    })
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeTruthy()
  })

  it('leaves the signal alone when nothing was written before the 429', async () => {
    const detail = { message: 'slow down', refreshed: 0, skipped: 3, rate_limited: 1 }
    reject429(detail)
    await expect(assets.refreshPrices('ws-1')).rejects.toMatchObject({
      response: { status: 429, data: { detail } },
    })
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeNull()
  })
})

describe('transfer responses carry numeric pocket amounts', () => {
  const leg = (id: string, amount: string) => ({
    id,
    goal_allocations: [{ id: `${id}-a`, goal_id: 'goal-1', goal_name: 'Trip', amount }],
  })
  const respondWith = (body: unknown) => {
    api.defaults.adapter = (config: InternalAxiosRequestConfig) =>
      Promise.resolve({ data: body, status: 200, statusText: 'OK', headers: {}, config })
  }

  beforeEach(() => localStorage.clear())

  it('normalizes both legs of a linked transfer and a created counterpart', async () => {
    const pair = { debit: leg('d', '-20.50'), credit: leg('c', '30.00'), transfer_pair_id: 'p' }
    respondWith(pair)
    for (const result of [
      await transactions.linkTransfer(['d', 'c']),
      await transactions.createTransferCounterpart('d', 'acc-2'),
    ]) {
      expect(result.debit.goal_allocations?.[0].amount).toBe(-20.5)
      expect(result.credit.goal_allocations?.[0].amount).toBe(30)
      expect(result.transfer_pair_id).toBe('p')
    }
  })

  it('normalizes transfer candidates and the transfer pair, and keeps a missing pair null', async () => {
    respondWith([leg('x', '12.34')])
    expect((await transactions.transferCandidates('t'))[0].goal_allocations?.[0].amount).toBe(12.34)
    respondWith(leg('y', '-5.00'))
    expect((await transactions.transferPair('t'))?.goal_allocations?.[0].amount).toBe(-5)
    respondWith(null)
    expect(await transactions.transferPair('t')).toBeNull()
  })
})
