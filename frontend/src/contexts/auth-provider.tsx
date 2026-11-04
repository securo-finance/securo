import { useState, useEffect, useCallback, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { auth, info } from '@/lib/api'
import type { User } from '@/types'

import { AuthContext, type LoginResult } from '@/contexts/auth-context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))
  const [isLoading, setIsLoading] = useState(true)
  // Demo mode auto-logs-in the seeded user on first load. Hold the
  // logged-out guard below until that first no-token resolution finishes so
  // the login form never flashes before a demo visitor is signed in. For
  // self-hosted users it flips true almost immediately (one /api/info call).
  const [bootResolved, setBootResolved] = useState(false)
  const queryClient = useQueryClient()

  if (!token && bootResolved && (user !== null || isLoading)) {
    setUser(null)
    setIsLoading(false)
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
    info.get()
      .then(({ features }) => {
        if (!features?.demo) return
        return info.demoLogin().then(({ access_token }) => {
          if (cancelled) return
          localStorage.setItem('token', access_token)
          setToken(access_token)
        })
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setBootResolved(true) })
    return () => { cancelled = true }
  }, [token])

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
    setToken(accessToken)
    const me = await auth.me()
    setUser(me)
    return { requires_2fa: false }
  }, [])

  const verify2fa = useCallback(async (tempToken: string, code: string) => {
    const data = await auth.verify2fa(tempToken, code)
    localStorage.setItem('token', data.access_token)
    setToken(data.access_token)
    const me = await auth.me()
    setUser(me)
  }, [])

  const loginWithToken = useCallback((accessToken: string) => {
    localStorage.setItem('token', accessToken)
    setToken(accessToken)
    auth.me().then(setUser).catch(() => {})
  }, [])

  const updateUser = useCallback((updatedUser: User) => {
    setUser(updatedUser)
  }, [])

  const register = useCallback(async (email: string, password: string, preferences?: Record<string, string>) => {
    await auth.register(email, password, preferences)
    await login(email, password)
  }, [login])

  const logout = useCallback(() => {
    localStorage.removeItem('token')
    setToken(null)
    setUser(null)
    queryClient.clear()
  }, [queryClient])

  return (
    <AuthContext.Provider value={{ user, token, isLoading, login, verify2fa, loginWithToken, register, updateUser, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
