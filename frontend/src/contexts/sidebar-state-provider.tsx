import { useState, useCallback, useMemo, type ReactNode } from 'react'

import { SidebarStateContext, type SidebarStateValue } from '@/contexts/sidebar-state-context'

export const SIDEBAR_COLLAPSED_STORAGE_KEY = 'securo.sidebar.collapsed'

export function SidebarStateProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true',
  )

  const toggleCollapsed = useCallback(() => {
    setCollapsed((current) => {
      const next = !current
      localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(next))
      return next
    })
  }, [])

  const value = useMemo(
    () => ({ collapsed, toggleCollapsed }) satisfies SidebarStateValue,
    [collapsed, toggleCollapsed],
  )

  return (
    <SidebarStateContext.Provider value={value}>{children}</SidebarStateContext.Provider>
  )
}
