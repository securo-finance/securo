import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { DATA_CHANGED_KEY } from '@/lib/data-changed'

/**
 * Refetches the cached queries when data changes in another tab.
 *
 * The `storage` event only fires in tabs that did not write, so this
 * complements the per-mutation `invalidateQueries` calls that already keep
 * the current tab in sync (issue #763): a rule re-applied from another tab
 * used to leave this tab's transaction lists stale until the 5-minute
 * staleTime expired.
 */
export function useCrossTabRefresh(): void {
  const queryClient = useQueryClient()

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== DATA_CHANGED_KEY) return
      queryClient.invalidateQueries()
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [queryClient])
}
