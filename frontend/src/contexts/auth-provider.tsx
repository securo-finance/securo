import { useState, useEffect, useCallback, type ReactNode } from 'react'
import { isAxiosError } from 'axios'
import { useQueryClient } from '@tanstack/react-query'
import { auth } from '@/lib/api'
import type { User } from '@/types'

import { AuthContext, type LoginResult } from '@/contexts/auth-context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))
  const [isLoading, setIsLoading] = useState(true)
  const [retryCount, setRetryCount] = useState(0)
  const queryClient = useQueryClient()

  if (!token && (user !== null || isLoading)) {
    setUser(null)
    setIsLoading(false)
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
          setToken(null)
          return
        }
        setUser(null)
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
