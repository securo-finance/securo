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
  const { pluginsEnabled, isLoading: flagsLoading, isError: flagsFailed, isKnown: flagsKnown } = useFeatureFlags()
  const { data, isError } = useQuery({
    queryKey: ['plugins', 'installed'],
    // Read only when fetching: with plugins off the API is never touched.
    queryFn: () => pluginsApi.installed(),
    enabled: pluginsEnabled,
    staleTime: 5 * 60 * 1000,
  })
  const entries = useMemo(() => {
    // Unread flags (loading, failed, or paused offline) say nothing about plugins: keep what was saved.
    if (flagsLoading || flagsFailed || flagsKnown === false) return null
    if (!pluginsEnabled) return []
    return data ? pluginReportEntries(data) : null
  }, [data, flagsLoading, flagsFailed, flagsKnown, pluginsEnabled])
  // Settled once there's nothing left to wait for: the flags failed for good, or they were
  // read and either plugins are off or their list (or its failure) is in. Not while the
  // flags are paused offline, so the page waits to open on a saved plugin report.
  const flagsRead = flagsKnown !== false
  const settled = !flagsLoading && (flagsFailed || (flagsRead && (!pluginsEnabled || data !== undefined || isError)))
  return { entries, settled }
}
