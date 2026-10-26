import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, it, vi } from 'vitest'
import type { BankConnection } from '@/types'
import { isGatewayTimeout, waitForNewConnection } from './await-new-connection'

function axiosError(status?: number): AxiosError {
  const config = { headers: new AxiosHeaders() }
  return new AxiosError(
    'failed',
    undefined,
    config,
    undefined,
    status === undefined ? undefined : { status, statusText: '', headers: {}, config, data: {} },
  )
}

const conn = (id: string, provider = 'simplefin', user_id = 'me') =>
  ({ id, provider, user_id }) as BankConnection

describe('isGatewayTimeout', () => {
  it('is true when no response came back or a gateway gave up', () => {
    expect(isGatewayTimeout(axiosError())).toBe(true)
    expect(isGatewayTimeout(axiosError(502))).toBe(true)
    expect(isGatewayTimeout(axiosError(504))).toBe(true)
  })

  it('is false for an answer from the backend itself', () => {
    expect(isGatewayTimeout(axiosError(400))).toBe(false)
    expect(isGatewayTimeout(axiosError(500))).toBe(false)
    expect(isGatewayTimeout(new Error('boom'))).toBe(false)
  })
})

describe('waitForNewConnection', () => {
  it('returns the connection that appears after the timeout', async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce([conn('old')])
      .mockRejectedValueOnce(new Error('504'))
      .mockResolvedValueOnce([conn('old'), conn('other', 'pluggy'), conn('new')])

    const found = await waitForNewConnection('simplefin', new Set(['old']), 'me', {
      intervalMs: 1,
      timeoutMs: 1000,
      list,
    })

    expect(found?.id).toBe('new')
    expect(list).toHaveBeenCalledTimes(3)
  })

  it('gives up with null when two new connections make it ambiguous', async () => {
    const list = vi.fn().mockResolvedValue([conn('old'), conn('mine'), conn('theirs')])

    const found = await waitForNewConnection('simplefin', new Set(['old']), 'me', {
      intervalMs: 1,
      timeoutMs: 1000,
      list,
    })

    expect(found).toBeNull()
    expect(list).toHaveBeenCalledTimes(1)
  })

  it('gives up with null when nothing new appears', async () => {
    const list = vi.fn().mockResolvedValue([conn('old'), conn('other', 'pluggy')])

    const found = await waitForNewConnection('simplefin', new Set(['old']), 'me', {
      intervalMs: 1,
      timeoutMs: 20,
      list,
    })

    expect(found).toBeNull()
  })

  it('ignores a connection another workspace member added meanwhile', async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce([conn('old'), conn('teammate', 'simplefin', 'someone-else')])
      .mockResolvedValueOnce([conn('old'), conn('teammate', 'simplefin', 'someone-else'), conn('new')])

    const found = await waitForNewConnection('simplefin', new Set(['old']), 'me', {
      intervalMs: 1,
      timeoutMs: 1000,
      list,
    })

    expect(found?.id).toBe('new')
    expect(list).toHaveBeenCalledTimes(2)
  })
})
