import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { plugins as pluginsApi } from '@/lib/api'

const NO_ICONS: ReadonlyMap<string, string> = new Map()

/**
 * Installed plugins' icons, by plugin id, as the catalog lists them.
 *
 * Only the catalog knows a plugin's icon, so this reads it, sharing the
 * Plugins page's query. Pass `enabled` only when a plugin avatar is on screen:
 * the catalog is a request to the plugin index. A plugin missing from the
 * catalog, or a catalog that can't be reached, leaves its generated badge.
 */
export function usePluginIcons(enabled: boolean): ReadonlyMap<string, string> {
  const { data } = useQuery({
    queryKey: ['plugins', 'catalog'],
    queryFn: pluginsApi.catalog,
    staleTime: 5 * 60 * 1000,
    retry: false,
    enabled,
  })
  return useMemo(
    () =>
      data
        ? new Map(data.flatMap((entry) => (entry.icon_url ? [[entry.id, entry.icon_url] as const] : [])))
        : NO_ICONS,
    [data],
  )
}
