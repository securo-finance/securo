import { createContext, useContext } from 'react'
import type { User } from '@/types'

export interface LoginResult {
  requires_2fa: boolean
  temp_token?: string
  available_methods?: Array<'totp' | 'passkey'>
}

interface AuthContextType {
  user: User | null
  token: string | null
  isLoading: boolean
  /** A token is kept but the server did not answer for it (offline, 5xx). */
  isUnreachable: boolean
  /** Ask the server for the session again. */
  retrySession: () => void
  login: (email: string, password: string) => Promise<LoginResult>
  verify2fa: (tempToken: string, code: string) => Promise<void>
  /** `viaOidc` marks the session as coming from the provider, so signing out
   *  can end the provider's session too (issue #1623). */
  loginWithToken: (accessToken: string, viaOidc?: boolean) => void
  register: (email: string, password: string, preferences?: Record<string, string>) => Promise<void>
  updateUser: (user: User) => void
  logout: () => void
}

export const AuthContext = createContext<AuthContextType | null>(null)

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
