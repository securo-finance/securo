import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { plugins as pluginsApi } from '@/lib/api'
import { pluginReportEntries, type PluginReportEntry } from '@/lib/library'

/**
 * The reports the workspace's installed plugins add to the library (ADR 0028).
 * Read from what was installed, never the catalog; none while plugins are off.
 * `entries` is null until it's known, so a saved tab isn't dropped meanwhile.
 */
export function usePluginReports(): { entries: PluginReportEntry[] | null } {
  const { pluginsEnabled, isLoading: flagsLoading } = useFeatureFlags()
  const { data, isError } = useQuery({
    queryKey: ['plugins', 'installed'],
    // Read only when fetching: with plugins off the API is never touched.
    queryFn: () => pluginsApi.installed(),
    enabled: pluginsEnabled,
    staleTime: 5 * 60 * 1000,
  })
  const entries = useMemo(() => {
    if (flagsLoading) return null
    if (!pluginsEnabled || isError) return []
    return data ? pluginReportEntries(data) : null
  }, [data, flagsLoading, isError, pluginsEnabled])
  return { entries }
}
