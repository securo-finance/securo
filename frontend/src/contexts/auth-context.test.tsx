import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AxiosError } from 'axios'

import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import type { User } from '@/types'
import { createTestQueryClient } from '@/test/utils'

const auth = vi.hoisted(() => ({
  me: vi.fn(),
  login: vi.fn(),
  verify2fa: vi.fn(),
  register: vi.fn(),
  oidcConfig: vi.fn(),
}))
const info = vi.hoisted(() => ({
  get: vi.fn(),
  demoLogin: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ auth, info }))

/** Self-hosted default: demo flag off, so no auto-login is attempted. */
const NOT_A_DEMO = { version: '0.20.0', revision: null, features: { agents: false, demo: false } }

const providerLogout = vi.hoisted(() => ({ endProviderSession: vi.fn() }))
vi.mock('@/lib/provider-logout', () => providerLogout)

const USER: User = {
  id: '1',
  email: 'tassio@example.com',
  is_active: true,
  is_superuser: false,
  is_verified: true,
  is_2fa_enabled: false,
  preferences: {},
}

/** An axios failure that carries an HTTP response, like a real 401 or 502. */
function httpError(status: number): AxiosError {
  const error = new AxiosError(`HTTP ${status}`)
  error.response = { status } as AxiosError['response']
  return error
}

/** An axios failure with no response: offline, DNS, connection refused. */
function networkError(): AxiosError {
  return new AxiosError('Network Error')
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
  info.get.mockResolvedValue(NOT_A_DEMO)
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

  it('signs a visitor in automatically when the server is in demo mode', async () => {
    // The public demo has no login form: /api/info reports demo=true and the
    // provider mints a session for the seeded demo user before the first paint.
    info.get.mockResolvedValue({ version: '0.20.0', revision: null, features: { agents: false, demo: true } })
    info.demoLogin.mockResolvedValue({ access_token: 'demo-jwt' })
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()

    expect(info.demoLogin).toHaveBeenCalled()
    expect(localStorage.getItem('token')).toBe('demo-jwt')
    expect(result.current.token).toBe('demo-jwt')
    await waitFor(() => expect(result.current.user).toEqual(USER))
  })

  it('settles signed out when the demo login is refused', async () => {
    // A demo instance whose seed has not run yet (503) must fall back to the
    // login screen instead of hanging on the boot guard.
    info.get.mockResolvedValue({ version: '0.20.0', revision: null, features: { agents: false, demo: true } })
    info.demoLogin.mockRejectedValue(new Error('503'))

    const { result } = await renderAuth()

    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
  })

  it('discards a token the server rejects', async () => {
    // An expired or revoked token must not leave the app in a half-signed-in
    // state where every request 401s.
    localStorage.setItem('token', 'expired-jwt')
    auth.me.mockRejectedValue(httpError(401))

    const { result } = await renderAuth()

    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('keeps the token when the server cannot be reached', async () => {
    // Opening the app offline (or while the server restarts) must not sign the
    // user out: the token is still good once the connection is back.
    localStorage.setItem('token', 'stored-jwt')
    auth.me.mockRejectedValue(networkError())

    const { result } = await renderAuth()

    expect(result.current.token).toBe('stored-jwt')
    expect(result.current.user).toBeNull()
    expect(localStorage.getItem('token')).toBe('stored-jwt')
  })

  it('keeps the token on a server error', async () => {
    localStorage.setItem('token', 'stored-jwt')
    auth.me.mockRejectedValue(httpError(502))

    const { result } = await renderAuth()

    expect(result.current.token).toBe('stored-jwt')
    expect(localStorage.getItem('token')).toBe('stored-jwt')
  })

  it('marks an unreachable session, so the shell can say the data is unavailable', async () => {
    // Issue #292: keeping the token but rendering the app left the user with
    // an empty dashboard. The flag lets the shell show an offline state.
    localStorage.setItem('token', 'stored-jwt')
    auth.me.mockRejectedValue(networkError())

    const { result } = await renderAuth()

    expect(result.current.sessionUnreachable).toBe(true)
    expect(result.current.token).toBe('stored-jwt')
  })

  it('does not mark a rejected token as unreachable', async () => {
    localStorage.setItem('token', 'expired-jwt')
    auth.me.mockRejectedValue(httpError(401))

    const { result } = await renderAuth()

    expect(result.current.sessionUnreachable).toBe(false)
  })

  it('checks the session again when the person asks to retry', async () => {
    localStorage.setItem('token', 'stored-jwt')
    auth.me.mockRejectedValueOnce(networkError()).mockResolvedValue(USER)

    const { result } = await renderAuth()
    expect(result.current.sessionUnreachable).toBe(true)

    act(() => { result.current.retrySession() })

    await waitFor(() => expect(result.current.user).toEqual(USER))
    expect(result.current.sessionUnreachable).toBe(false)
    expect(auth.me).toHaveBeenCalledTimes(2)
  })

  it('marks a token adopted from OIDC as unreachable when it cannot be checked', async () => {
    auth.me.mockRejectedValue(networkError())

    const { result } = await renderAuth()
    act(() => { result.current.loginWithToken('oidc-jwt', true) })

    await waitFor(() => expect(result.current.sessionUnreachable).toBe(true))
    expect(result.current.token).toBe('oidc-jwt')
  })

  it('loads the user once the connection is back', async () => {
    localStorage.setItem('token', 'stored-jwt')
    auth.me.mockRejectedValueOnce(networkError()).mockResolvedValue(USER)

    const { result } = await renderAuth()
    expect(result.current.user).toBeNull()

    act(() => { window.dispatchEvent(new Event('online')) })

    await waitFor(() => expect(result.current.user).toEqual(USER))
    expect(auth.me).toHaveBeenCalledTimes(2)
  })

  it('retries when the connection returns while the first check is still pending', async () => {
    localStorage.setItem('token', 'stored-jwt')
    let rejectFirst: (error: unknown) => void = () => {}
    auth.me
      .mockReturnValueOnce(new Promise((_, reject) => { rejectFirst = reject }))
      .mockResolvedValue(USER)

    const result = renderHook(() => useAuth(), { wrapper })
    act(() => { window.dispatchEvent(new Event('online')) })
    await act(async () => { rejectFirst(networkError()) })

    await waitFor(() => expect(result.result.current.user).toEqual(USER))
    expect(auth.me).toHaveBeenCalledTimes(2)
  })

  it('stops listening for retries once the user is loaded', async () => {
    localStorage.setItem('token', 'stored-jwt')
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()
    await waitFor(() => expect(result.current.user).toEqual(USER))

    act(() => { window.dispatchEvent(new Event('online')) })

    expect(auth.me).toHaveBeenCalledTimes(1)
  })

  it('drops the previous user when another tab swaps the token and the check fails', async () => {
    localStorage.setItem('token', 'old-jwt')
    auth.me.mockResolvedValueOnce(USER).mockRejectedValue(networkError())

    const { result } = await renderAuth()
    await waitFor(() => expect(result.current.user).toEqual(USER))

    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: 'token', newValue: 'new-jwt' }))
    })

    await waitFor(() => expect(result.current.user).toBeNull())
    expect(result.current.token).toBe('new-jwt')
  })
})

describe('AuthProvider automatic reconnect', () => {
  /** Let pending timers and promise callbacks run, inside act. */
  async function advance(ms: number) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
  }

  it('checks again by itself when the server returns with no browser event', async () => {
    // Issue #292: a server restart while the browser stays online and the tab
    // visible fires neither online nor visibilitychange. The offline screen
    // says it reconnects on its own, so a timer has to ask again.
    vi.useFakeTimers()
    try {
      localStorage.setItem('token', 'stored-jwt')
      auth.me
        .mockRejectedValueOnce(httpError(503))
        .mockRejectedValueOnce(httpError(503))
        .mockResolvedValue(USER)

      const { result } = renderHook(() => useAuth(), { wrapper })
      await advance(0)
      expect(result.current.sessionUnreachable).toBe(true)
      expect(auth.me).toHaveBeenCalledTimes(1)

      await advance(1_999)
      expect(auth.me).toHaveBeenCalledTimes(1)
      await advance(1)
      expect(auth.me).toHaveBeenCalledTimes(2)
      expect(result.current.sessionUnreachable).toBe(true)

      // The wait doubles after a second failure.
      await advance(3_999)
      expect(auth.me).toHaveBeenCalledTimes(2)
      await advance(1)
      expect(result.current.user).toEqual(USER)
      expect(result.current.sessionUnreachable).toBe(false)

      // Back online: no more checks.
      await advance(60_000)
      expect(auth.me).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('stops the automatic checks when the provider unmounts', async () => {
    vi.useFakeTimers()
    try {
      localStorage.setItem('token', 'stored-jwt')
      auth.me.mockRejectedValue(networkError())

      const { result, unmount } = renderHook(() => useAuth(), { wrapper })
      await advance(0)
      expect(result.current.sessionUnreachable).toBe(true)

      unmount()
      await advance(120_000)
      expect(auth.me).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not let a superseded loginWithToken check undo a validated session', async () => {
    // loginWithToken checks the token, and the token change starts the
    // effect's own check. If the first fails late, after the second loaded the
    // user, it must not put the offline screen over a working session.
    const { result } = await renderAuth()
    let rejectFirst: (error: unknown) => void = () => {}
    auth.me
      .mockReturnValueOnce(new Promise((_, reject) => { rejectFirst = reject }))
      .mockResolvedValue(USER)

    act(() => { result.current.loginWithToken('oidc-jwt', true) })
    await waitFor(() => expect(result.current.user).toEqual(USER))

    await act(async () => { rejectFirst(networkError()) })

    expect(result.current.sessionUnreachable).toBe(false)
    expect(result.current.user).toEqual(USER)
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

describe('AuthProvider sign-out', () => {
  it('ends the provider session when the sign-in came from OIDC (issue #1623)', async () => {
    localStorage.setItem('token', 'oidc-jwt')
    localStorage.setItem('oidc_session', '1')
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()
    act(() => result.current.logout())

    expect(providerLogout.endProviderSession).toHaveBeenCalledTimes(1)
    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('oidc_session')).toBeNull()
  })

  it('leaves the provider alone on a local sign-out', async () => {
    localStorage.setItem('token', 'local-jwt')
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()
    act(() => result.current.logout())

    expect(providerLogout.endProviderSession).not.toHaveBeenCalled()
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('does not end the provider session when a sign-in finished meanwhile', async () => {
    localStorage.setItem('token', 'oidc-jwt')
    localStorage.setItem('oidc_session', '1')
    auth.me.mockResolvedValue(USER)
    let shouldRedirect: (() => boolean) | undefined
    providerLogout.endProviderSession.mockImplementation(
      (options: { shouldRedirect?: () => boolean }) => {
        shouldRedirect = options.shouldRedirect
      },
    )

    const { result } = await renderAuth()
    act(() => result.current.logout())
    expect(shouldRedirect?.()).toBe(true)

    act(() => result.current.loginWithToken('new-jwt'))
    expect(shouldRedirect?.()).toBe(false)

    // A session that came and went still took over the browser: looking only
    // at the stored token would let the late provider redirect through.
    localStorage.removeItem('token')
    expect(shouldRedirect?.()).toBe(false)
  })

  it('marks a session as OIDC only when loginWithToken says so', async () => {
    auth.me.mockResolvedValue(USER)

    const { result } = await renderAuth()
    act(() => result.current.loginWithToken('oidc-jwt', true))
    expect(localStorage.getItem('oidc_session')).toBe('1')

    act(() => result.current.loginWithToken('local-jwt'))
    expect(localStorage.getItem('oidc_session')).toBeNull()
  })
})

describe('useAuth', () => {
  it('refuses to be used outside the provider', () => {
    expect(() => renderHook(() => useAuth())).toThrow(
      'useAuth must be used within an AuthProvider',
    )
  })
})
