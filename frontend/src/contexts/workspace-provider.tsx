import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/contexts/auth-context'
import { workspaces as workspacesApi, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import type { ModuleId } from '@/lib/modules'
import type { Workspace } from '@/types'
import { WorkspaceContext } from '@/contexts/workspace-context'

const NO_WORKSPACES: Workspace[] = []

/**
 * Fetch the workspaces the signed-in person can access and reconcile the
 * stored selection against them.
 *
 * If the stored ID is stale (workspace archived, user removed, etc.) it falls
 * back to the first accessible one. Kept out of the component so both the
 * sign-in effect and the `refresh` callback share one implementation, and so
 * neither has to set state synchronously.
 */
async function fetchWorkspaces(): Promise<{ list: Workspace[]; currentId: string | null }> {
  const fetched = await workspacesApi.list()
  const storedId = localStorage.getItem(WORKSPACE_STORAGE_KEY)
  const found = fetched.find((w) => w.id === storedId)
  if (found) return { list: fetched, currentId: found.id }
  if (fetched.length > 0) {
    const fallbackId = fetched[0].id
    localStorage.setItem(WORKSPACE_STORAGE_KEY, fallbackId)
    return { list: fetched, currentId: fallbackId }
  }
  localStorage.removeItem(WORKSPACE_STORAGE_KEY)
  return { list: fetched, currentId: null }
}


export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user, token, isLoading: authLoading } = useAuth()
  const [list, setList] = useState<Workspace[]>([])
  const [currentId, setCurrentId] = useState<string | null>(() => localStorage.getItem(WORKSPACE_STORAGE_KEY))
  const [isLoading, setIsLoading] = useState(true)
  const queryClient = useQueryClient()

  const loadWorkspaces = useCallback(async () => {
    try {
      const next = await fetchWorkspaces()
      setList(next.list)
      setCurrentId(next.currentId)
    } catch {
      // 401s are handled by the global interceptor; other failures we
      // just surface as no-data — the user can retry from the UI.
      setList([])
    } finally {
      setIsLoading(false)
    }
  }, [])

  const signedOut = !authLoading && (!user || !token)

  useEffect(() => {
    // Stay in the loading state until auth has settled. Reporting "done,
    // no workspaces" while the token is still being restored is a lie
    // that lasts one render — long enough for anything gated on
    // `hasModule` to decide the module is off and redirect away.
    if (authLoading || signedOut) return
    let cancelled = false
    void (async () => {
      try {
        const next = await fetchWorkspaces()
        if (cancelled) return
        setList(next.list)
        setCurrentId(next.currentId)
      } catch {
        // 401s are handled by the global interceptor; other failures we
        // just surface as no-data — the user can retry from the UI.
        if (!cancelled) setList([])
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [authLoading, signedOut])

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
        current: signedOut ? null : current,
        workspaces: signedOut ? NO_WORKSPACES : list,
        isLoading: signedOut ? false : isLoading,
        switchWorkspace,
        refresh: loadWorkspaces,
        role,
        canManage,
        canWrite,
        enabledModules,
        hasModule,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  )
}
