import { useState, useEffect, useCallback, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { auth } from '@/lib/api'
import { revokeDashboardAuthentication } from '@/lib/dashboard-layout-auth-bridge'
import type { User } from '@/types'

import { AuthContext, type LoginResult } from '@/contexts/auth-context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))
  const [isLoading, setIsLoading] = useState(true)
  const queryClient = useQueryClient()

  if (!token && (user !== null || isLoading)) {
    setUser(null)
    setIsLoading(false)
  }

  useEffect(() => {
    if (!token) return
    let cancelled = false
    auth.me()
      .then((me) => { if (!cancelled) setUser(me) })
      .catch(() => {
        if (cancelled) return
        revokeDashboardAuthentication(queryClient)
        localStorage.removeItem('token')
        setToken(null)
      })
      .finally(() => { if (!cancelled) setIsLoading(false) })
    return () => { cancelled = true }
  }, [token, queryClient])

  // Sync token across tabs via storage events
  useEffect(() => {
    const handleStorage = (e: StorageEvent) => {
      if (e.key === 'token') {
        if (e.oldValue !== e.newValue) revokeDashboardAuthentication(queryClient)
        setToken(e.newValue)
      }
    }
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [queryClient])

  const login = useCallback(async (email: string, password: string): Promise<LoginResult> => {
    const data = await auth.login(email, password)

    if (data.requires_2fa) {
      return { requires_2fa: true, temp_token: data.temp_token, available_methods: data.available_methods }
    }

    const accessToken = data.access_token
    revokeDashboardAuthentication(queryClient)
    localStorage.setItem('token', accessToken)
    setToken(accessToken)
    const me = await auth.me()
    setUser(me)
    return { requires_2fa: false }
  }, [queryClient])

  const verify2fa = useCallback(async (tempToken: string, code: string) => {
    const data = await auth.verify2fa(tempToken, code)
    revokeDashboardAuthentication(queryClient)
    localStorage.setItem('token', data.access_token)
    setToken(data.access_token)
    const me = await auth.me()
    setUser(me)
  }, [queryClient])

  const loginWithToken = useCallback((accessToken: string) => {
    revokeDashboardAuthentication(queryClient)
    localStorage.setItem('token', accessToken)
    setToken(accessToken)
    auth.me().then(setUser).catch(() => {})
  }, [queryClient])

  const updateUser = useCallback((updatedUser: User) => {
    setUser(updatedUser)
  }, [])

  const register = useCallback(async (email: string, password: string, preferences?: Record<string, string>) => {
    await auth.register(email, password, preferences)
    await login(email, password)
  }, [login])

  const logout = useCallback(() => {
    revokeDashboardAuthentication(queryClient)
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
