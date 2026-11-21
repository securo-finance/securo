import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { isAxiosError } from 'axios'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/contexts/auth-context'
import { workspaces as workspacesApi, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import type { ModuleId } from '@/lib/modules'
import { reconnectDelay } from '@/lib/reconnect-delay'
import type { Workspace } from '@/types'
import { WorkspaceContext } from '@/contexts/workspace-context'

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user, token, isLoading: authLoading } = useAuth()
  const [list, setList] = useState<Workspace[]>([])
  const [currentId, setCurrentId] = useState<string | null>(() => localStorage.getItem(WORKSPACE_STORAGE_KEY))
  const [loadingList, setLoadingList] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  // The token whose list we last fetched (or failed to). A session that comes
  // back after being unreachable keeps its token but gets a user again; the
  // render before the fetch starts must still count as loading, or module
  // routes read every module as off and send the page home (issue #292).
  const [fetchedFor, setFetchedFor] = useState<string | null>(null)
  const isLoading = loadingList || (!!user && !!token && fetchedFor !== token)
  // Failed loads in a row; paces the automatic retries.
  const [failedLoads, setFailedLoads] = useState(0)
  // Bumped by every load and by sign-out: only the latest load may write its
  // result, so an older request that fails late cannot flag a list a newer
  // one already brought back, or clear it.
  const loadGeneration = useRef(0)
  const queryClient = useQueryClient()

  const loadWorkspaces = useCallback(async () => {
    const load = ++loadGeneration.current
    setLoadingList(true)
    try {
      const fetched = await workspacesApi.list()
      if (load !== loadGeneration.current) return
      setLoadFailed(false)
      setFailedLoads(0)
      setList(fetched)
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
    } catch (error) {
      if (load !== loadGeneration.current) return
      // 401s are handled by the global interceptor. Anything else means the
      // server could not be asked, not that the user has no workspaces: mark
      // it so the shell says so instead of treating every module as off and
      // redirecting the requested page home (issue #292).
      if (!(isAxiosError(error) && error.response?.status === 401)) {
        setLoadFailed(true)
        setFailedLoads((n) => n + 1)
      }
      setList([])
    } finally {
      if (load === loadGeneration.current) setLoadingList(false)
    }
  }, [])

  useEffect(() => {
    // Stay in the loading state until auth has settled. Reporting "done,
    // no workspaces" while the token is still being restored is a lie
    // that lasts one render — long enough for anything gated on
    // `hasModule` to decide the module is off and redirect away.
    if (authLoading) return
    if (!user || !token) {
      loadGeneration.current += 1
      setFailedLoads(0)
      setList([])
      setCurrentId(null)
      setLoadFailed(false)
      setLoadingList(false)
      setFetchedFor(null)
      return
    }
    let cancelled = false
    void loadWorkspaces().finally(() => {
      if (!cancelled) setFetchedFor(token)
    })
    return () => {
      cancelled = true
    }
  }, [authLoading, user, token, loadWorkspaces])

  // A failed list must heal by itself once the connection is back: the URL the
  // person opened is still there and the page can load without a new sign-in.
  useEffect(() => {
    if (!loadFailed || !user || !token) return
    const retry = () => {
      if (document.visibilityState === 'hidden') return
      void loadWorkspaces()
    }
    window.addEventListener('online', retry)
    document.addEventListener('visibilitychange', retry)
    return () => {
      window.removeEventListener('online', retry)
      document.removeEventListener('visibilitychange', retry)
    }
  }, [loadFailed, user, token, loadWorkspaces])

  // The server can come back with no browser event (a restart while the
  // browser stays online), so also ask again on a timer: 2 s, doubling up to
  // 30 s. Each failed load schedules the next; recovery, sign-out or unmount
  // clears it. A hidden tab waits for visibilitychange instead.
  useEffect(() => {
    if (!loadFailed || !user || !token || failedLoads === 0) return
    const timer = window.setTimeout(() => {
      if (document.visibilityState === 'hidden') return
      void loadWorkspaces()
    }, reconnectDelay(failedLoads))
    return () => window.clearTimeout(timer)
  }, [loadFailed, user, token, failedLoads, loadWorkspaces])

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

  const current = useMemo(() => list.find((w) => w.id === currentId) ?? null, [list, currentId])

  const role = current?.role ?? null
  const canManage = role === 'owner' || role === 'manager'
  const canWrite = role === 'owner' || role === 'manager' || role === 'editor'

  const enabledModules = useMemo(() => (current?.enabled_modules ?? []) as ModuleId[], [current])
  const hasModule = useCallback((id: ModuleId) => enabledModules.includes(id), [enabledModules])

  return (
    <WorkspaceContext.Provider
      value={{
        current,
        workspaces: list,
        isLoading,
        loadFailed,
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
