import { Navigate } from 'react-router-dom'
import { useFeatureFlags } from '@/hooks/use-feature-flags'

/** The plugins page exists only while PLUGINS_ENABLED is on; otherwise go home. */
export function PluginsRoute({ children }: { children: React.ReactNode }) {
  const { pluginsEnabled, isLoading } = useFeatureFlags()
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }
  if (!pluginsEnabled) return <Navigate to="/" replace />
  return <>{children}</>
}
