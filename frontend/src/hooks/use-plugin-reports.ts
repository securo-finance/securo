import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { plugins as pluginsApi } from '@/lib/api'
import { pluginReportEntries, type PluginReportEntry } from '@/lib/library'

/**
 * The reports the workspace's installed plugins add to the library (ADR 0028).
 * Read from what was installed, never the catalog; none while plugins are off.
 * `entries` is null while it isn't known (still loading, or the read
 * failed), so a saved tab is never dropped for it; `settled` turns true once
 * there's nothing more to wait for.
 */
export function usePluginReports(): { entries: PluginReportEntry[] | null; settled: boolean } {
  const { pluginsEnabled, isLoading: flagsLoading, isError: flagsFailed } = useFeatureFlags()
  const { data, isError } = useQuery({
    queryKey: ['plugins', 'installed'],
    // Read only when fetching: with plugins off the API is never touched.
    queryFn: () => pluginsApi.installed(),
    enabled: pluginsEnabled,
    staleTime: 5 * 60 * 1000,
  })
  const entries = useMemo(() => {
    // Unread flags say nothing about plugins: keep what was saved.
    if (flagsLoading || flagsFailed) return null
    if (!pluginsEnabled) return []
    return data ? pluginReportEntries(data) : null
  }, [data, flagsLoading, flagsFailed, pluginsEnabled])
  return { entries, settled: !flagsLoading && (!pluginsEnabled || data !== undefined || isError) }
}
