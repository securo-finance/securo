import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { collections as collectionsApi } from '@/lib/api'
import { useWorkspace } from '@/contexts/workspace-context'
import type { Collection } from '@/types'

type CollectionFilterValue = {
  collections: Collection[]
  activeCollectionId: string | null
  activeCollection: Collection | null
  setActiveCollectionId: (id: string | null) => void
  // null = all accounts (no filter); otherwise the active collection's account ids.
  activeAccountIds: string[] | null
  // null = no filter; otherwise the active collection's wallet (asset_group) ids.
  activeWalletIds: string[] | null
}

const CollectionFilterContext = createContext<CollectionFilterValue | null>(null)
const STORAGE_PREFIX = 'securo.activeCollection.'

export function CollectionFilterProvider({ children }: { children: ReactNode }) {
  const { current } = useWorkspace()
  const wsId = current?.id ?? ''

  const { data } = useQuery({
    queryKey: ['collections'],
    queryFn: collectionsApi.list,
  })
  const collections = useMemo(() => data ?? [], [data])

  const [activeCollectionId, setActiveId] = useState<string | null>(null)

  // The active selection is persisted per workspace — switching workspaces
  // restores that workspace's last-used collection (or "all").
  const [loadedWsId, setLoadedWsId] = useState<string | null>(null)
  if (wsId && wsId !== loadedWsId) {
    setLoadedWsId(wsId)
    setActiveId(localStorage.getItem(STORAGE_PREFIX + wsId) || null)
  }

  const setActiveCollectionId = (id: string | null) => {
    setActiveId(id)
    if (!wsId) return
    if (id) localStorage.setItem(STORAGE_PREFIX + wsId, id)
    else localStorage.removeItem(STORAGE_PREFIX + wsId)
  }

  const activeCollection = useMemo(
    () => collections.find((c) => c.id === activeCollectionId) ?? null,
    [collections, activeCollectionId],
  )

  // If the active collection was deleted elsewhere, fall back to "all".
  useEffect(() => {
    if (activeCollectionId && collections.length > 0 && !activeCollection) {
      setActiveCollectionId(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCollectionId, collections, activeCollection])

  const value: CollectionFilterValue = {
    collections,
    activeCollectionId,
    activeCollection,
    setActiveCollectionId,
    activeAccountIds: activeCollection ? activeCollection.account_ids : null,
    activeWalletIds: activeCollection ? activeCollection.wallet_ids : null,
  }

  return (
    <CollectionFilterContext.Provider value={value}>{children}</CollectionFilterContext.Provider>
  )
}

export function useCollectionFilter(): CollectionFilterValue {
  const ctx = useContext(CollectionFilterContext)
  if (!ctx) {
    throw new Error('useCollectionFilter must be used within a CollectionFilterProvider')
  }
  return ctx
}
