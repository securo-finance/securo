import { Navigate } from 'react-router-dom'
import { useAuth } from '@/contexts/auth-context'
import { ConnectionLost } from '@/components/connection-lost'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { token, isLoading, sessionUnreachable, retrySession } = useAuth()

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

  // The token is still stored; the server just could not be asked. Showing the
  // shell here would render empty dashboards and send module routes home, so
  // say the data is unavailable and keep the URL for when the server is back.
  if (sessionUnreachable) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background">
        <ConnectionLost onRetry={retrySession} />
      </div>
    )
  }

  return <>{children}</>
}
