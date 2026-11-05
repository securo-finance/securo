import { createContext, useContext } from 'react'
import type { SessionStore } from '@/lib/dashboard-layout-session'

export type DashboardLayoutOwner = Readonly<{
  actorId: string
  authGeneration: number
  store: SessionStore
}>

export const DashboardLayoutContext = createContext<DashboardLayoutOwner | null | undefined>(undefined)

export function useDashboardLayoutOwner(): DashboardLayoutOwner | null {
  const owner = useContext(DashboardLayoutContext)
  if (owner === undefined) throw new Error('useDashboardLayoutOwner must be used within a DashboardLayoutProvider')
  return owner
}
