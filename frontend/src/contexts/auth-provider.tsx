import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react'
import { isAxiosError } from 'axios'
import { useQueryClient } from '@tanstack/react-query'
import { auth, info } from '@/lib/api'
import type { User } from '@/types'

import { AuthContext, type LoginResult } from '@/contexts/auth-context'
import { endProviderSession } from '@/lib/provider-logout'
import { reconnectDelay } from '@/lib/reconnect-delay'

/** Marks a session that came from the OIDC provider (issue #1623). */
const OIDC_SESSION_KEY = 'oidc_session'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))
  const [isLoading, setIsLoading] = useState(true)
  // Demo mode auto-logs-in the seeded user on first load. Hold the
  // logged-out guard below until that first no-token resolution finishes so
  // the login form never flashes before a demo visitor is signed in. For
  // self-hosted users it flips true almost immediately (one /api/info call).
  const [bootResolved, setBootResolved] = useState(false)
  // The stored token is still good; we just could not ask the server. Kept
  // apart from `user === null` so the shell can tell "signed out" and "could
  // not load" apart (issue #292).
  const [sessionUnreachable, setSessionUnreachable] = useState(false)
  const [retryCount, setRetryCount] = useState(0)
  // Failed checks in a row while unreachable; paces the automatic retries.
  const [failedChecks, setFailedChecks] = useState(0)
  const queryClient = useQueryClient()
  // Bumped by every sign-in: a provider logout that resolves after a new
  // session started must not redirect and end it (#1623).
  const signInGeneration = useRef(0)
  // Bumped by every session check: only the latest one may set the user or
  // the unreachable flag, so a slow, superseded check cannot undo a newer one.
  const checkGeneration = useRef(0)

  // A session that goes away mid-flight (logout here or in another tab) is
  // signed out at once, without waiting for /api/info again; only the very
  // first boot, when there was never a user, waits for that answer so the
  // login form never flashes before the demo auto-login lands.
  if (!token && (user !== null || sessionUnreachable || failedChecks !== 0 || (isLoading && bootResolved))) {
    setUser(null)
    setIsLoading(false)
    setSessionUnreachable(false)
    setFailedChecks(0)
  }

  useEffect(() => {
    if (token) {
      let cancelled = false
      auth.me()
        .then((me) => { if (!cancelled) setUser(me) })
        .catch(() => {
          if (cancelled) return
          localStorage.removeItem('token')
          setToken(null)
        })
        .finally(() => { if (!cancelled) setIsLoading(false) })
      return () => { cancelled = true }
    }
    // No token. In demo mode, mint a JWT for the seeded demo user so
    // visitors land straight on the dashboard. /api/info reports demo=true
    // only when DEMO_MODE is on; demoLogin 404s otherwise, a no-op for
    // self-hosted users. Setting the token re-runs this effect, which then
    // takes the branch above; `bootResolved` releases the logged-out guard.
    let cancelled = false
    const check = ++checkGeneration.current
    const superseded = () => cancelled || check !== checkGeneration.current
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
    const checkSession = () => {
      auth.me()
        .then((me) => {
          if (superseded()) return
          stopRetrying()
          setSessionUnreachable(false)
          setFailedChecks(0)
          setUser(me)
        })
        .catch((error) => {
          if (superseded()) return
          // Only a token the server rejects ends the session. No response (offline,
          // server restarting) or a 5xx says nothing about the token, so keep it and
          // drop any user loaded for a previous token; the listeners retry.
          if (isAxiosError(error) && error.response?.status === 401) {
            stopRetrying()
            localStorage.removeItem('token')
            localStorage.removeItem(OIDC_SESSION_KEY)
            setSessionUnreachable(false)
            setToken(null)
            return
          }
          setSessionUnreachable(true)
          setFailedChecks((n) => n + 1)
          setUser(null)
        })
        .finally(() => { if (!cancelled) setIsLoading(false) })
    }
    // Demo mode mints a JWT for the seeded demo user so visitors land straight
    // on the dashboard. /api/info reports demo=true only when DEMO_MODE is on,
    // and demoLogin 404s otherwise, so a self-hosted install just runs the
    // ordinary check. `bootResolved` releases the logged-out guard above once
    // the answer is in, so the login form never flashes first.
    info.get()
      .then(({ features }) => {
        if (superseded()) return
        if (!features?.demo) {
          checkSession()
          return
        }
        return info.demoLogin()
          .then(({ access_token }) => {
            if (superseded()) return
            localStorage.setItem('token', access_token)
            setToken(access_token)
          })
          .catch(() => { if (!superseded()) checkSession() })
      })
      .catch(() => { if (!superseded()) checkSession() })
      .finally(() => { if (!cancelled) setBootResolved(true) })
    return () => {
      cancelled = true
      stopRetrying()
    }
  }, [token, retryCount])

  // A server that restarts while the browser stays online and the tab visible
  // fires no event to retry on, so ask again on a timer: 2 s, doubling up to
  // 30 s. Each failed check schedules the next; recovery, sign-out or unmount
  // clears it. A hidden tab waits for visibilitychange instead (issue #292).
  useEffect(() => {
    if (!sessionUnreachable || !token || failedChecks === 0) return
    const timer = window.setTimeout(() => {
      if (document.visibilityState === 'hidden') return
      setRetryCount((n) => n + 1)
    }, reconnectDelay(failedChecks))
    return () => window.clearTimeout(timer)
  }, [sessionUnreachable, token, failedChecks])

  // The offline screen's "Try again": go back to the spinner while the check
  // runs, so the person sees the click was taken.
  const retrySession = useCallback(() => {
    if (!localStorage.getItem('token')) return
    setIsLoading(true)
    setRetryCount((n) => n + 1)
  }, [])

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
    // A new token also starts the effect's check, which supersedes this one;
    // this one still settles a token that did not change.
    const check = ++checkGeneration.current
    auth.me()
      .then((me) => {
        if (check !== checkGeneration.current) return
        setSessionUnreachable(false)
        setFailedChecks(0)
        setUser(me)
      })
      .catch((error) => {
        if (check !== checkGeneration.current) return
        if (!(isAxiosError(error) && error.response?.status === 401)) {
          setSessionUnreachable(true)
          setFailedChecks((n) => n + 1)
        }
      })
  }, [])

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
    <AuthContext.Provider value={{ user, token, isLoading, sessionUnreachable, retrySession, login, verify2fa, loginWithToken, register, updateUser, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
