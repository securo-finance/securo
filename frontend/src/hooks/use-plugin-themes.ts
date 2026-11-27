import { useQuery } from '@tanstack/react-query'

import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { plugins as pluginsApi } from '@/lib/api'
import { pluginTheme, type Theme } from '@/lib/themes'

/**
 * The themes the workspace's installed plugins bring, for the library.
 * Read from what was installed, never the catalog; nothing while plugins are off.
 */
export function usePluginThemes(): { themes: Theme[]; loading: boolean } {
  const { pluginsEnabled, isLoading: flagsLoading } = useFeatureFlags()
  const { data, isLoading } = useQuery({
    queryKey: ['plugins', 'installed'],
    // Read only when fetching: with plugins off the API is never touched.
    queryFn: () => pluginsApi.installed(),
    enabled: pluginsEnabled,
    staleTime: 5 * 60 * 1000,
  })
  const themes = (data ?? []).flatMap((plugin) => (plugin.themes ?? []).map((theme) => pluginTheme(plugin, theme)))
  // Still loading until the flags say whether there are plugins at all.
  return { themes, loading: flagsLoading || (pluginsEnabled && isLoading) }
}
