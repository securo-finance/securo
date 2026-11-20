import { useCallback } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { ServerUnreachable } from '@/components/server-unreachable'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { token, isLoading, isUnreachable, retrySession } = useAuth()
  const { loadFailed, refresh } = useWorkspace()

  const retry = useCallback(() => {
    if (isUnreachable) retrySession()
    else void refresh()
  }, [isUnreachable, retrySession, refresh])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  if (!token) {
    return <Navigate to="/login" replace />
  }

  // The token is kept but nothing could be loaded for it. The pages are
  // not rendered at all: they would show zero balances and redirect module
  // routes to home.
  if (isUnreachable || loadFailed) {
    return <ServerUnreachable onRetry={retry} />
  }

  return <>{children}</>
}
