import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { AuthProvider, useAuth } from '@/contexts/auth-context'
import type { User } from '@/types'
import { createTestQueryClient } from '@/test/utils'

const auth = vi.hoisted(() => ({
  me: vi.fn(),
  login: vi.fn(),
  verify2fa: vi.fn(),
  register: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ auth }))

const USER: User = {
  id: '1',
  email: 'tassio@example.com',
  is_active: true,
  is_superuser: false,
  is_verified: true,
  is_2fa_enabled: false,
  preferences: {},
}

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={createTestQueryClient()}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  )
}

/** Render the hook and wait for the initial session probe to settle. */
async function renderAuth() {
  const result = renderHook(() => useAuth(), { wrapper })
  await waitFor(() => expect(result.result.current.isLoading).toBe(false))
  return result
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

describe('AuthProvider session restore', () => {
  it('settles as signed out when there is no stored token', async () => {
    const { result } = await renderAuth()

    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
    expect(auth.me).not.toHaveBeenCalled()
  })

  it('restores the user from a stored token', async () => {
    localStorage.setItem('token', 'stored-jwt')
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()

    expect(auth.me).toHaveBeenCalled()
    expect(result.current.user).toEqual(USER)
    expect(result.current.token).toBe('stored-jwt')
  })

  it('discards a token the server rejects', async () => {
    // An expired or revoked token must not leave the app in a half-signed-in
    // state where every request 401s.
    localStorage.setItem('token', 'expired-jwt')
    auth.me.mockRejectedValue(new Error('401'))

    const { result } = await renderAuth()

    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
    expect(localStorage.getItem('token')).toBeNull()
  })
})

describe('login', () => {
  it('stores the token and loads the user', async () => {
    auth.login.mockResolvedValue({ access_token: 'new-jwt' })
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()

    await act(async () => {
      const outcome = await result.current.login('tassio@example.com', 'pw')
      expect(outcome).toEqual({ requires_2fa: false })
    })

    expect(localStorage.getItem('token')).toBe('new-jwt')
    await waitFor(() => expect(result.current.user).toEqual(USER))
  })

  it('does not store a session when a second factor is still required', async () => {
    // The temp token is not a session. Persisting it here would sign the user
    // in on a refresh without them ever passing 2FA.
    auth.login.mockResolvedValue({
      requires_2fa: true,
      temp_token: 'temp',
      available_methods: ['totp'],
    })

    const { result } = await renderAuth()

    let outcome!: Awaited<ReturnType<typeof result.current.login>>
    await act(async () => {
      outcome = await result.current.login('tassio@example.com', 'pw')
    })

    expect(outcome).toEqual({
      requires_2fa: true,
      temp_token: 'temp',
      available_methods: ['totp'],
    })
    expect(localStorage.getItem('token')).toBeNull()
    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
  })

  it('propagates a rejected login instead of swallowing it', async () => {
    auth.login.mockRejectedValue(new Error('invalid credentials'))

    const { result } = await renderAuth()

    await expect(
      act(async () => {
        await result.current.login('tassio@example.com', 'wrong')
      }),
    ).rejects.toThrow('invalid credentials')

    expect(localStorage.getItem('token')).toBeNull()
  })
})

describe('verify2fa', () => {
  it('exchanges the temp token for a real session', async () => {
    auth.verify2fa.mockResolvedValue({ access_token: 'jwt-after-2fa' })
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()

    await act(async () => {
      await result.current.verify2fa('temp', '123456')
    })

    expect(auth.verify2fa).toHaveBeenCalledWith('temp', '123456')
    expect(localStorage.getItem('token')).toBe('jwt-after-2fa')
    await waitFor(() => expect(result.current.user).toEqual(USER))
  })
})

describe('loginWithToken', () => {
  it('adopts a token minted elsewhere, such as the OIDC callback', async () => {
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()

    act(() => {
      result.current.loginWithToken('oidc-jwt')
    })

    expect(localStorage.getItem('token')).toBe('oidc-jwt')
    await waitFor(() => expect(result.current.user).toEqual(USER))
  })
})

describe('register', () => {
  it('signs the new account straight in', async () => {
    auth.register.mockResolvedValue(undefined)
    auth.login.mockResolvedValue({ access_token: 'jwt' })
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()

    await act(async () => {
      await result.current.register('new@example.com', 'pw', { language: 'pt-BR' })
    })

    expect(auth.register).toHaveBeenCalledWith('new@example.com', 'pw', {
      language: 'pt-BR',
    })
    expect(auth.login).toHaveBeenCalledWith('new@example.com', 'pw')
    expect(localStorage.getItem('token')).toBe('jwt')
  })

  it('does not attempt a login when registration fails', async () => {
    auth.register.mockRejectedValue(new Error('email taken'))

    const { result } = await renderAuth()

    await expect(
      act(async () => {
        await result.current.register('taken@example.com', 'pw')
      }),
    ).rejects.toThrow('email taken')

    expect(auth.login).not.toHaveBeenCalled()
  })
})

describe('logout', () => {
  it('clears the token and the user', async () => {
    localStorage.setItem('token', 'jwt')
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()
    await waitFor(() => expect(result.current.user).toEqual(USER))

    act(() => {
      result.current.logout()
    })

    expect(localStorage.getItem('token')).toBeNull()
    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
  })

  it('empties the query cache, so the next account sees no stale balances', async () => {
    // This is the privacy-relevant half of logout. Signing out on a shared
    // machine has to drop the cached financial data, not just the token.
    localStorage.setItem('token', 'jwt')
    auth.me.mockResolvedValue(USER)

    // Not createTestQueryClient: its gcTime of 0 collects data that has no
    // active observer, so the fixture would vanish before logout ran and the
    // test would pass for the wrong reason.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    queryClient.setQueryData(['accounts'], [{ id: 'a1', balance: 4200 }])

    function localWrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          <AuthProvider>{children}</AuthProvider>
        </QueryClientProvider>
      )
    }

    const { result } = renderHook(() => useAuth(), { wrapper: localWrapper })
    await waitFor(() => expect(result.current.user).toEqual(USER))
    expect(queryClient.getQueryData(['accounts'])).toBeDefined()

    act(() => {
      result.current.logout()
    })

    expect(queryClient.getQueryData(['accounts'])).toBeUndefined()
  })
})

describe('updateUser', () => {
  it('replaces the cached user without another round trip', async () => {
    localStorage.setItem('token', 'jwt')
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()
    await waitFor(() => expect(result.current.user).toEqual(USER))

    const renamed = { ...USER, email: 'renamed@example.com' }
    act(() => {
      result.current.updateUser(renamed)
    })

    expect(result.current.user).toEqual(renamed)
    expect(auth.me).toHaveBeenCalledTimes(1)
  })
})

describe('cross-tab sync', () => {
  it('signs out when another tab clears the token', async () => {
    localStorage.setItem('token', 'jwt')
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()
    await waitFor(() => expect(result.current.user).toEqual(USER))

    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', { key: 'token', newValue: null }),
      )
    })

    await waitFor(() => expect(result.current.token).toBeNull())
    expect(result.current.user).toBeNull()
  })

  it('ignores storage events for unrelated keys', async () => {
    localStorage.setItem('token', 'jwt')
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()
    await waitFor(() => expect(result.current.user).toEqual(USER))

    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', { key: 'theme', newValue: 'light' }),
      )
    })

    expect(result.current.token).toBe('jwt')
  })
})

describe('useAuth', () => {
  it('refuses to be used outside the provider', () => {
    expect(() => renderHook(() => useAuth())).toThrow(
      'useAuth must be used within an AuthProvider',
    )
  })
})
