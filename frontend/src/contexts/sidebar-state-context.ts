import { createContext, useContext } from 'react'

export type SidebarStateValue = {
  /** Whether the desktop sidebar is collapsed to its narrow rail (desktop only). */
  collapsed: boolean
  /** Toggle the desktop sidebar's collapsed state (persists to localStorage). */
  toggleCollapsed: () => void
}

export const SidebarStateContext = createContext<SidebarStateValue | null>(null)

export function useSidebarState(): SidebarStateValue {
  const ctx = useContext(SidebarStateContext)
  if (!ctx) {
    throw new Error('useSidebarState must be used within a SidebarStateProvider')
  }
  return ctx
}
