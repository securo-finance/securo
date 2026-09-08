import { createContext, useContext } from 'react'
import type { Collection } from '@/types'

export type CollectionFilterValue = {
  collections: Collection[]
  activeCollectionId: string | null
  activeCollection: Collection | null
  setActiveCollectionId: (id: string | null) => void
  // null = all accounts (no filter); otherwise the active collection's account ids.
  activeAccountIds: string[] | null
  // null = no filter; otherwise the active collection's wallet (asset_group) ids.
  activeWalletIds: string[] | null
}

export const CollectionFilterContext = createContext<CollectionFilterValue | null>(null)

export function useCollectionFilter(): CollectionFilterValue {
  const ctx = useContext(CollectionFilterContext)
  if (!ctx) {
    throw new Error('useCollectionFilter must be used within a CollectionFilterProvider')
  }
  return ctx
}
