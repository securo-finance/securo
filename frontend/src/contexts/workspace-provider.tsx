import { createContext, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/contexts/auth-context'
import { workspaces as workspacesApi, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import type { ModuleId } from '@/lib/modules'
import type { Workspace } from '@/types'
import { WorkspaceContext } from '@/contexts/workspace-context'


// Local refresh state, not a draft store: consumers retain their own edits.
export const WorkspaceRefreshStatusContext = createContext({ pending: false, failed: false })

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user, token } = useAuth()
  // Remount the complete workspace subtree on auth identity/logout, even if
  // the next identity can access the same workspace ID. No old draft survives.
  return <WorkspaceSessionProvider key={user && token ? user.id : 'signed-out'}>{children}</WorkspaceSessionProvider>
}

function WorkspaceSessionProvider({ children }: { children: ReactNode }) {
  const { user, token, isLoading: authLoading } = useAuth()
  const [list, setList] = useState<Workspace[]>([])
  const [currentId, setCurrentId] = useState<string | null>(() => localStorage.getItem(WORKSPACE_STORAGE_KEY))
  const [isLoading, setIsLoading] = useState(true)
  const [refreshFailed, setRefreshFailed] = useState(false)
  const queryClient = useQueryClient()

  const loadGeneration = useRef(0)
  useEffect(() => () => { ++loadGeneration.current }, [])
  const loadWorkspaces = useCallback(async () => {
    const generation = ++loadGeneration.current
    if (!user || !token) return
    setIsLoading(true)
    try {
      const fetched = await workspacesApi.list()
      if (generation !== loadGeneration.current) return
      setList(fetched)
      setRefreshFailed(false)
      // Reconcile the stored selection against what's actually accessible.
      // If the stored ID is stale (workspace archived, user removed, etc.)
      // fall back to the first one.
      const storedId = localStorage.getItem(WORKSPACE_STORAGE_KEY)
      const found = fetched.find((w) => w.id === storedId)
      if (found) {
        setCurrentId(found.id)
      } else if (fetched.length > 0) {
        const fallbackId = fetched[0].id
        localStorage.setItem(WORKSPACE_STORAGE_KEY, fallbackId)
        setCurrentId(fallbackId)
      } else {
        localStorage.removeItem(WORKSPACE_STORAGE_KEY)
        setCurrentId(null)
      }
    } catch {
      if (generation !== loadGeneration.current) return
      // Keep the last usable SAME-AUTH list so mounted local drafts survive.
      // Initial failures still expose an empty list. Never reject refresh:
      // existing fire-and-forget callers rely on its resolving contract.
      setRefreshFailed(true)
    } finally {
      if (generation === loadGeneration.current) setIsLoading(false)
    }
  }, [user, token])

  useEffect(() => {
    // Stay in the loading state until auth has settled. Reporting "done,
    // no workspaces" while the token is still being restored is a lie
    // that lasts one render — long enough for anything gated on
    // `hasModule` to decide the module is off and redirect away.
    if (authLoading) return
    if (!user || !token) {
      setList([])
      setCurrentId(null)
      setIsLoading(false)
      return
    }
    void loadWorkspaces()
  }, [authLoading, user, token, loadWorkspaces])

  const switchWorkspace = useCallback(
    async (id: string) => {
      if (id === currentId) return
      // Persist FIRST so the axios interceptor sends the new
      // workspace_id on every refetch fired below.
      localStorage.setItem(WORKSPACE_STORAGE_KEY, id)
      setCurrentId(id)
      // Every cached query was scoped to the previous workspace.
      // `resetQueries` flushes cached data AND refetches active
      // observers in one call — `clear()` alone removed data without
      // triggering refetches (mounted components kept their previous
      // render until a manual reload).
      await queryClient.resetQueries()
    },
    [currentId, queryClient],
  )

  const current = useMemo(
    () => list.find((w) => w.id === currentId) ?? null,
    [list, currentId],
  )

  const role = current?.role ?? null
  const canManage = role === 'owner' || role === 'manager'
  const canWrite = role === 'owner' || role === 'manager' || role === 'editor'

  const enabledModules = useMemo(
    () => (current?.enabled_modules ?? []) as ModuleId[],
    [current],
  )
  const hasModule = useCallback(
    (id: ModuleId) => enabledModules.includes(id),
    [enabledModules],
  )

  return (
    <WorkspaceContext.Provider
      value={{
        current,
        workspaces: list,
        isLoading,
        switchWorkspace,
        refresh: loadWorkspaces,
        role,
        canManage,
        canWrite,
        enabledModules,
        hasModule,
      }}
    >
      <WorkspaceRefreshStatusContext.Provider value={{ pending: isLoading, failed: refreshFailed }}>
        {children}
      </WorkspaceRefreshStatusContext.Provider>
    </WorkspaceContext.Provider>
  )
}
