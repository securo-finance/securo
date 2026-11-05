/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { InternalAxiosRequestConfig } from 'axios'
import { api, rules, transactions } from '@/lib/api'
import { DATA_CHANGED_KEY } from '@/lib/data-changed'

// The interceptor under test only looks at method + success, so a fake
// adapter that always resolves 200 is all the transport it needs.
const okAdapter = (config: InternalAxiosRequestConfig) =>
  Promise.resolve({ data: {}, status: 200, statusText: 'OK', headers: {}, config })

describe('import option serialization', () => {
  beforeEach(() => { api.defaults.adapter = okAdapter; vi.restoreAllMocks() })

  it.each([undefined, false, true])('serializes sign override %s without losing false', async (flip) => {
    const post = vi.spyOn(api, 'post')
    await transactions.previewImport(new File(['Date,Description,Amount\n'], 'synthetic.csv'), { preset: 'monarch', flip_amount: flip })
    const form = post.mock.calls[0][1] as FormData
    expect(form.has('flip_amount')).toBe(flip !== undefined)
    if (flip !== undefined) expect(form.get('flip_amount')).toBe(String(flip))
    expect(form.get('preset')).toBe('monarch')
  })

  it('keeps duplicate detection disabled on the real import client', async () => {
    const post = vi.spyOn(api, 'post')
    await transactions.import('synthetic-account', [], 'synthetic.csv', 'csv', { detect_duplicates: false })
    expect(post.mock.calls[0][1]).toEqual(expect.objectContaining({ detect_duplicates: false }))
  })
})

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
