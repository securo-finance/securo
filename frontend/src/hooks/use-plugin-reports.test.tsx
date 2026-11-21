import { describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'

import { usePluginReports } from '@/hooks/use-plugin-reports'
import { createTestQueryClient } from '@/test/utils'

const flags = vi.hoisted(() => ({ isLoading: false, isError: false, isKnown: false, pluginsEnabled: false, agentsEnabled: false }))
vi.mock('@/hooks/use-feature-flags', () => ({ useFeatureFlags: () => flags }))
vi.mock('@/lib/api', () => ({
  plugins: {
    installed: async () => [{
      plugin_id: 'maria/casa', name: { en: 'Home' },
      reports: [{ key: 'pets', name: { en: 'Pets' }, measure: 'expenses', group_by: 'month', chart: 'bar' }],
    }],
  },
}))

describe('usePluginReports', () => {
  it('waits while the flags are paused offline, and settles with the reports once they come back', async () => {
    const client = createTestQueryClient()
    const { result, rerender } = renderHook(() => usePluginReports(), {
      wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    })
    expect(result.current).toEqual({ entries: null, settled: false })

    // Reconnected: the flags say plugins are on, and the list comes in.
    flags.isKnown = true
    flags.pluginsEnabled = true
    rerender()
    await waitFor(() => expect(result.current.settled).toBe(true))
    expect(result.current.entries?.map((entry) => entry.id)).toEqual(['plugin:maria/casa:pets'])
  })
})
