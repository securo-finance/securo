import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'

import { useLocalAuthEnabled } from '@/hooks/use-local-auth'
import { createTestQueryClient } from '@/test/utils'

const authApi = vi.hoisted(() => ({ oidcConfig: vi.fn() }))
vi.mock('@/lib/api', () => ({ auth: authApi }))

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

describe('useLocalAuthEnabled', () => {
  it('shows the local credential controls when the server allows them', async () => {
    authApi.oidcConfig.mockResolvedValue({
      enabled: true,
      provider_name: 'Keycloak',
      local_auth_enabled: true,
    })

    const { result } = renderHook(() => useLocalAuthEnabled(), { wrapper })

    await waitFor(() => expect(result.current).toBe(true))
  })

  it('hides them in an OIDC-only deployment', async () => {
    authApi.oidcConfig.mockResolvedValue({
      enabled: true,
      provider_name: 'Keycloak',
      local_auth_enabled: false,
    })

    const { result } = renderHook(() => useLocalAuthEnabled(), { wrapper })

    await waitFor(() => expect(result.current).toBe(false))
  })

  it('settles rather than hanging when the config call fails', async () => {
    // The hook pins retry:false and networkMode:'always' precisely so a
    // failure reaches the fallback instead of parking the query in pending,
    // which would hide the controls indefinitely.
    authApi.oidcConfig.mockRejectedValue(new Error('offline'))

    const { result } = renderHook(() => useLocalAuthEnabled(), { wrapper })

    await waitFor(() => expect(result.current).toBe(true))
    expect(authApi.oidcConfig).toHaveBeenCalledTimes(1)
  })
})
