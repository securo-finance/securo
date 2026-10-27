/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DATA_CHANGED_KEY, markDataChanged } from '@/lib/data-changed'

describe('markDataChanged', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('writes a value under the signal key', () => {
    markDataChanged()
    expect(localStorage.getItem(DATA_CHANGED_KEY)).toBeTruthy()
  })

  it('writes a new value on each call, even within the same millisecond', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1)
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    markDataChanged()
    const first = localStorage.getItem(DATA_CHANGED_KEY)
    markDataChanged()
    const second = localStorage.getItem(DATA_CHANGED_KEY)
    expect(second).not.toBe(first)
  })

  it('does not throw when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(() => markDataChanged()).not.toThrow()
  })
})
