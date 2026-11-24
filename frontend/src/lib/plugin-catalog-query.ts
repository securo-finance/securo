import { plugins as pluginsApi } from '@/lib/api'

/**
 * The catalog query, shared by the Plugins page and the plugin avatars outside
 * it (ADR 0026).
 *
 * Both observers write their options onto the same cache entry and the last
 * one to render wins, so the options live here: a `retry` that differed
 * between them would silently change the other's behaviour.
 */
export const pluginCatalogQuery = {
  queryKey: ['plugins', 'catalog'],
  queryFn: pluginsApi.catalog,
  staleTime: 5 * 60 * 1000,
  retry: 1,
} as const
