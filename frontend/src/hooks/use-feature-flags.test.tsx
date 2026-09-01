import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'

import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { createTestQueryClient } from '@/test/utils'

const info = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api', () => ({ info }))

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={createTestQueryClient()}>
      {children}
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useFeatureFlags', () => {
  it('reports agents on when the server says so', async () => {
    info.get.mockResolvedValue({ features: { agents: true } })

    const { result } = renderHook(() => useFeatureFlags(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.agentsEnabled).toBe(true)
  })

  it('reports agents off when the server says so', async () => {
    info.get.mockResolvedValue({ features: { agents: false } })

    const { result } = renderHook(() => useFeatureFlags(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.agentsEnabled).toBe(false)
  })

  it('defaults to off while still loading', () => {
    // AgentsRoute reads this. Defaulting to on would flash a management page
    // whose every call 404s on a deployment that never enabled agents.
    info.get.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useFeatureFlags(), { wrapper })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.agentsEnabled).toBe(false)
  })

  it('defaults to off when /api/info omits the features block', async () => {
    // An older backend behind a newer frontend.
    info.get.mockResolvedValue({})

    const { result } = renderHook(() => useFeatureFlags(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.agentsEnabled).toBe(false)
  })

  it('defaults to off when the request fails', async () => {
    info.get.mockRejectedValue(new Error('offline'))

    const { result } = renderHook(() => useFeatureFlags(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.agentsEnabled).toBe(false)
  })
})
