import { describe, expect, it } from 'vitest'

import { reconnectDelay } from './reconnect-delay'

describe('reconnectDelay', () => {
  it('starts at 2 s and doubles after each failure', () => {
    expect([1, 2, 3, 4].map(reconnectDelay)).toEqual([2_000, 4_000, 8_000, 16_000])
  })

  it('never waits more than 30 s', () => {
    expect(reconnectDelay(5)).toBe(30_000)
    expect(reconnectDelay(50)).toBe(30_000)
  })

  it('treats zero failures like the first', () => {
    expect(reconnectDelay(0)).toBe(2_000)
  })
})
