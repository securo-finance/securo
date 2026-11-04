import { Navigate } from 'react-router-dom'
import { useWorkspace } from '@/contexts/workspace-context'
import type { ModuleId } from '@/lib/modules'

/** Wraps a module's pages so navigating straight to the URL when the
 *  active workspace doesn't show that module lands on home instead of
 *  rendering it. Hiding the nav link alone leaves the page reachable.
 *  Mirrors AdminRoute / AgentsRoute. */
export function ModuleRoute({
  module,
  children,
}: {
  module: ModuleId
  children: React.ReactNode
}) {
  const { hasModule, isLoading } = useWorkspace()
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }
  if (!hasModule(module)) return <Navigate to="/" replace />
  return <>{children}</>
}
