import { useQuery } from '@tanstack/react-query'
import { info } from '@/lib/api'

/**
 * Reads /api/info once per session. Optional modules (agents, etc.) check
 * the returned flags so their nav items / routes stay hidden when the
 * server has the feature disabled.
 */
export function useFeatureFlags() {
  const { data, isLoading } = useQuery({
    queryKey: ['app-info'],
    queryFn: () => info.get(),
    staleTime: 1000 * 60 * 60,
  })
  return {
    isLoading,
    agentsEnabled: !!data?.features?.agents,
    // On unless the server says otherwise, matching the backend default.
    benchmarksEnabled: data?.features?.performance_benchmarks !== false,
  }
}
