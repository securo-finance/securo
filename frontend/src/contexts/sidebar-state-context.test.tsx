import type { ReactNode } from 'react'
import { describe, expect, it, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { SidebarStateProvider, SIDEBAR_COLLAPSED_STORAGE_KEY } from '@/contexts/sidebar-state-provider'
import { useSidebarState } from '@/contexts/sidebar-state-context'

function wrapper({ children }: { children: ReactNode }) {
  return <SidebarStateProvider>{children}</SidebarStateProvider>
}

beforeEach(() => {
  localStorage.clear()
})

describe('SidebarStateProvider', () => {
  it('starts expanded when nothing is stored', () => {
    const { result } = renderHook(() => useSidebarState(), { wrapper })

    expect(result.current.collapsed).toBe(false)
  })

  it('restores the collapsed state from localStorage', () => {
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, 'true')

    const { result } = renderHook(() => useSidebarState(), { wrapper })

    expect(result.current.collapsed).toBe(true)
  })

  it('treats any non-true value as expanded', () => {
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, 'banana')

    const { result } = renderHook(() => useSidebarState(), { wrapper })

    expect(result.current.collapsed).toBe(false)
  })

  it('toggles collapsed and persists the choice', () => {
    const { result } = renderHook(() => useSidebarState(), { wrapper })

    act(() => result.current.toggleCollapsed())
    expect(result.current.collapsed).toBe(true)
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('true')

    act(() => result.current.toggleCollapsed())
    expect(result.current.collapsed).toBe(false)
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('false')
  })

  it('flips a stored collapsed state back to expanded', () => {
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, 'true')

    const { result } = renderHook(() => useSidebarState(), { wrapper })

    act(() => result.current.toggleCollapsed())

    expect(result.current.collapsed).toBe(false)
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('false')
  })

  it('provides a stable value and toggle callback across renders', () => {
    const { result } = renderHook(() => useSidebarState(), { wrapper })

    const firstValue = result.current
    act(() => result.current.toggleCollapsed())
    const secondValue = result.current

    // The callback identity stays stable across re-renders.
    expect(firstValue.toggleCollapsed).toBe(secondValue.toggleCollapsed)
  })
})

describe('useSidebarState', () => {
  it('refuses to be used outside the provider', () => {
    expect(() => renderHook(() => useSidebarState())).toThrow(
      'useSidebarState must be used within a SidebarStateProvider',
    )
  })
})
