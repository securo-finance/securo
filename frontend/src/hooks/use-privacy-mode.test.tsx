import { beforeEach, describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { usePrivacyMode } from '@/hooks/use-privacy-mode'

beforeEach(() => {
  localStorage.clear()
})

describe('usePrivacyMode', () => {
  it('is off by default', () => {
    const { result } = renderHook(() => usePrivacyMode())

    expect(result.current.privacyMode).toBe(false)
  })

  it('reads an already-persisted preference on mount', () => {
    localStorage.setItem('privacyMode', 'true')

    const { result } = renderHook(() => usePrivacyMode())

    expect(result.current.privacyMode).toBe(true)
  })

  it('treats any other stored value as off', () => {
    localStorage.setItem('privacyMode', 'yes')

    const { result } = renderHook(() => usePrivacyMode())

    expect(result.current.privacyMode).toBe(false)
  })

  it('toggles and persists', () => {
    const { result } = renderHook(() => usePrivacyMode())

    act(() => result.current.togglePrivacyMode())

    expect(result.current.privacyMode).toBe(true)
    expect(localStorage.getItem('privacyMode')).toBe('true')

    act(() => result.current.togglePrivacyMode())

    expect(result.current.privacyMode).toBe(false)
    expect(localStorage.getItem('privacyMode')).toBe('false')
  })

  it('passes values through untouched while off', () => {
    const { result } = renderHook(() => usePrivacyMode())

    expect(result.current.mask('R$ 12.480,00')).toBe('R$ 12.480,00')
  })

  it('replaces every value with the mask while on', () => {
    const { result } = renderHook(() => usePrivacyMode())

    act(() => result.current.togglePrivacyMode())

    expect(result.current.mask('R$ 12.480,00')).toBe(result.current.MASK)
    expect(result.current.mask('-R$ 3,20')).toBe(result.current.MASK)
    // The mask must not leak the length of what it hides.
    expect(result.current.mask('R$ 1,00')).toBe(result.current.mask('R$ 999.999,00'))
  })

  it('keeps every subscriber in step, so one toggle hides the whole screen', () => {
    // Balances are rendered by many components at once. If they did not share
    // the store, toggling would blank some and leave others showing figures.
    const first = renderHook(() => usePrivacyMode())
    const second = renderHook(() => usePrivacyMode())

    act(() => first.result.current.togglePrivacyMode())

    expect(first.result.current.privacyMode).toBe(true)
    expect(second.result.current.privacyMode).toBe(true)
  })
})
