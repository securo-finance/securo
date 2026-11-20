import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react'
import { isAxiosError } from 'axios'
import { useQueryClient } from '@tanstack/react-query'
import { auth } from '@/lib/api'
import type { User } from '@/types'

import { AuthContext, type LoginResult } from '@/contexts/auth-context'
import { endProviderSession } from '@/lib/provider-logout'

/** Marks a session that came from the OIDC provider (issue #1623). */
const OIDC_SESSION_KEY = 'oidc_session'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))
  const [isLoading, setIsLoading] = useState(true)
  const [retryCount, setRetryCount] = useState(0)
  // The token is kept but the server could not confirm it (offline, 5xx):
  // the shell says so instead of rendering pages with no data (#292).
  const [isUnreachable, setIsUnreachable] = useState(false)
  const queryClient = useQueryClient()
  // Bumped by every sign-in: a provider logout that resolves after a new
  // session started must not redirect and end it (#1623).
  const signInGeneration = useRef(0)

  if (!token && (user !== null || isLoading || isUnreachable)) {
    setUser(null)
    setIsLoading(false)
    setIsUnreachable(false)
  }

  useEffect(() => {
    if (!token) return
    let cancelled = false
    // Ask again when the connection is back or the tab becomes visible. Listening
    // from the start covers an event that fires while the request is in flight.
    const retry = () => {
      if (document.visibilityState === 'hidden') return
      setRetryCount((n) => n + 1)
    }
    window.addEventListener('online', retry)
    document.addEventListener('visibilitychange', retry)
    const stopRetrying = () => {
      window.removeEventListener('online', retry)
      document.removeEventListener('visibilitychange', retry)
    }
    auth.me()
      .then((me) => {
        if (cancelled) return
        stopRetrying()
        setIsUnreachable(false)
        setUser(me)
      })
      .catch((error) => {
        if (cancelled) return
        // Only a token the server rejects ends the session. No response (offline,
        // server restarting) or a 5xx says nothing about the token, so keep it and
        // drop any user loaded for a previous token; the listeners retry.
        if (isAxiosError(error) && error.response?.status === 401) {
          stopRetrying()
          localStorage.removeItem('token')
          localStorage.removeItem(OIDC_SESSION_KEY)
          setToken(null)
          return
        }
        setUser(null)
        setIsUnreachable(true)
      })
      .finally(() => { if (!cancelled) setIsLoading(false) })
    return () => {
      cancelled = true
      stopRetrying()
    }
  }, [token, retryCount])

  // Sync token across tabs via storage events
  useEffect(() => {
    const handleStorage = (e: StorageEvent) => {
      if (e.key === 'token') {
        setToken(e.newValue)
      }
    }
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [])

  const login = useCallback(async (email: string, password: string): Promise<LoginResult> => {
    const data = await auth.login(email, password)

    if (data.requires_2fa) {
      return { requires_2fa: true, temp_token: data.temp_token, available_methods: data.available_methods }
    }

    const accessToken = data.access_token
    localStorage.setItem('token', accessToken)
    localStorage.removeItem(OIDC_SESSION_KEY)
    signInGeneration.current += 1
    setToken(accessToken)
    const me = await auth.me()
    setUser(me)
    return { requires_2fa: false }
  }, [])

  const verify2fa = useCallback(async (tempToken: string, code: string) => {
    const data = await auth.verify2fa(tempToken, code)
    localStorage.setItem('token', data.access_token)
    localStorage.removeItem(OIDC_SESSION_KEY)
    signInGeneration.current += 1
    setToken(data.access_token)
    const me = await auth.me()
    setUser(me)
  }, [])

  const loginWithToken = useCallback((accessToken: string, viaOidc = false) => {
    localStorage.setItem('token', accessToken)
    if (viaOidc) localStorage.setItem(OIDC_SESSION_KEY, '1')
    else localStorage.removeItem(OIDC_SESSION_KEY)
    signInGeneration.current += 1
    setToken(accessToken)
    auth.me().then(setUser).catch(() => {})
  }, [])

  const retrySession = useCallback(() => setRetryCount((n) => n + 1), [])

  const updateUser = useCallback((updatedUser: User) => {
    setUser(updatedUser)
  }, [])

  const register = useCallback(async (email: string, password: string, preferences?: Record<string, string>) => {
    await auth.register(email, password, preferences)
    await login(email, password)
  }, [login])

  const logout = useCallback(() => {
    const signedInWithOidc = localStorage.getItem(OIDC_SESSION_KEY) === '1'
    const generationAtSignOut = signInGeneration.current
    localStorage.removeItem('token')
    localStorage.removeItem(OIDC_SESSION_KEY)
    setToken(null)
    setUser(null)
    queryClient.clear()
    // Dropping the local token leaves the provider's SSO session alive, so the
    // next sign-in returns as the same user. Only an OIDC session has one to
    // end; a password or passkey sign-in never touches the provider.
    if (signedInWithOidc) {
      void endProviderSession({
        // The config request can take seconds: a sign-in that finished in the
        // meantime (here or in another tab) owns the browser now, and sending
        // it to the provider logout would end the session it just created.
        shouldRedirect: () =>
          signInGeneration.current === generationAtSignOut &&
          localStorage.getItem('token') === null,
      })
    }
  }, [queryClient])

  return (
    <AuthContext.Provider value={{ user, token, isLoading, isUnreachable, retrySession, login, verify2fa, loginWithToken, register, updateUser, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
