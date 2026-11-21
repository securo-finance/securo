import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'

import { WorkspaceProvider } from '@/contexts/workspace-provider'
import { useWorkspace } from '@/contexts/workspace-context'
import type { Workspace, WorkspaceRole } from '@/types'
import { createTestQueryClient } from '@/test/utils'

const workspacesApi = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('@/lib/api', () => ({
  workspaces: workspacesApi,
  WORKSPACE_STORAGE_KEY: 'workspace_id',
}))

const useAuth = vi.hoisted(() => vi.fn())
vi.mock('@/contexts/auth-context', () => ({ useAuth }))

function makeWorkspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    id: 'ws-1',
    name: 'Personal',
    kind: 'personal',
    role: 'owner',
    enabled_modules: ['transactions', 'accounts', 'budgets'],
    ...overrides,
  } as Workspace
}

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={createTestQueryClient()}>
      <WorkspaceProvider>{children}</WorkspaceProvider>
    </QueryClientProvider>
  )
}

async function renderWorkspace() {
  const rendered = renderHook(() => useWorkspace(), { wrapper })
  await waitFor(() => expect(rendered.result.current.isLoading).toBe(false))
  return rendered
}

function signedIn() {
  useAuth.mockReturnValue({
    user: { id: 'u1' },
    token: 'jwt',
    isLoading: false,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  signedIn()
})

describe('loading', () => {
  it('stays loading while auth is still resolving', async () => {
    // Reporting "done, no workspaces" for one render is long enough for
    // anything gated on hasModule to redirect away from a valid deep link.
    useAuth.mockReturnValue({ user: null, token: null, isLoading: true })

    const { result } = renderHook(() => useWorkspace(), { wrapper })

    expect(result.current.isLoading).toBe(true)
    expect(workspacesApi.list).not.toHaveBeenCalled()
  })

  it('reports empty once auth settles with no session', async () => {
    useAuth.mockReturnValue({ user: null, token: null, isLoading: false })

    const { result } = await renderWorkspace()

    expect(result.current.workspaces).toEqual([])
    expect(result.current.current).toBeNull()
    expect(workspacesApi.list).not.toHaveBeenCalled()
  })

  it('survives a failed fetch without getting stuck loading', async () => {
    workspacesApi.list.mockRejectedValue(new Error('network'))

    const { result } = await renderWorkspace()

    expect(result.current.workspaces).toEqual([])
  })

  it('flags a failed fetch as unreachable, not as "no workspaces"', async () => {
    // Issue #292: an empty list made hasModule() false and sent module routes
    // home. The flag tells the shell the workspace is unknown, not empty.
    workspacesApi.list.mockRejectedValue(new Error('network'))

    const { result } = await renderWorkspace()

    expect(result.current.loadFailed).toBe(true)
    expect(result.current.current).toBeNull()
  })

  it('does not flag a successful fetch', async () => {
    workspacesApi.list.mockResolvedValue([makeWorkspace()])

    const { result } = await renderWorkspace()

    expect(result.current.loadFailed).toBe(false)
  })

  it('stays loading when the session comes back until its list is fetched', async () => {
    // Issue #292: offline, auth keeps the token with no user, so the provider
    // settles as "no workspaces". When the user returns, the render before
    // the fetch starts must not report "loaded, every module off", or
    // ModuleRoute sends /transactions home.
    useAuth.mockReturnValue({ user: null, token: 'jwt', isLoading: false })
    let resolveList: (ws: Workspace[]) => void = () => {}
    workspacesApi.list.mockReturnValue(
      new Promise<Workspace[]>((resolve) => {
        resolveList = resolve
      }),
    )
    const seen: { isLoading: boolean; hasTransactions: boolean }[] = []
    const { result, rerender } = renderHook(
      () => {
        const ws = useWorkspace()
        seen.push({ isLoading: ws.isLoading, hasTransactions: ws.hasModule('transactions') })
        return ws
      },
      { wrapper },
    )
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    seen.length = 0
    signedIn()
    rerender()
    await act(async () => {
      resolveList([makeWorkspace()])
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.hasModule('transactions')).toBe(true)
    expect(seen.filter((s) => !s.isLoading && !s.hasTransactions)).toEqual([])
  })

  it('loads the workspaces once the connection is back', async () => {
    workspacesApi.list.mockRejectedValueOnce(new Error('network')).mockResolvedValue([makeWorkspace({ id: 'ws-a' })])

    const { result } = await renderWorkspace()
    expect(result.current.loadFailed).toBe(true)

    act(() => {
      window.dispatchEvent(new Event('online'))
    })

    await waitFor(() => expect(result.current.loadFailed).toBe(false))
    expect(result.current.current?.id).toBe('ws-a')
    expect(workspacesApi.list).toHaveBeenCalledTimes(2)
  })

  it('loads again by itself when the server returns with no browser event', async () => {
    // Issue #292: a server restart while the browser stays online and the tab
    // visible fires no event, so a timer asks again: 2 s, then 4 s.
    vi.useFakeTimers()
    const advance = async (ms: number) => {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(ms)
      })
    }
    try {
      workspacesApi.list
        .mockRejectedValueOnce(new Error('503'))
        .mockRejectedValueOnce(new Error('503'))
        .mockResolvedValue([makeWorkspace({ id: 'ws-a' })])

      const { result } = renderHook(() => useWorkspace(), { wrapper })
      await advance(0)
      expect(result.current.loadFailed).toBe(true)
      expect(workspacesApi.list).toHaveBeenCalledTimes(1)

      await advance(1_999)
      expect(workspacesApi.list).toHaveBeenCalledTimes(1)
      await advance(1)
      expect(workspacesApi.list).toHaveBeenCalledTimes(2)
      expect(result.current.loadFailed).toBe(true)

      await advance(3_999)
      expect(workspacesApi.list).toHaveBeenCalledTimes(2)
      await advance(1)
      expect(result.current.loadFailed).toBe(false)
      expect(result.current.current?.id).toBe('ws-a')

      await advance(60_000)
      expect(workspacesApi.list).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('stops the automatic loads when the provider unmounts', async () => {
    vi.useFakeTimers()
    try {
      workspacesApi.list.mockRejectedValue(new Error('503'))

      const { result, unmount } = renderHook(() => useWorkspace(), { wrapper })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(result.current.loadFailed).toBe(true)

      unmount()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(120_000)
      })
      expect(workspacesApi.list).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not let an older failed load undo a newer successful one', async () => {
    // The online event starts load A, then Retry starts load B. B brings the
    // list back; A failing afterwards must not flag it or clear it.
    workspacesApi.list.mockRejectedValueOnce(new Error('network'))
    const { result } = await renderWorkspace()
    expect(result.current.loadFailed).toBe(true)

    let rejectOlder: (error: unknown) => void = () => {}
    workspacesApi.list
      .mockReturnValueOnce(
        new Promise((_, reject) => {
          rejectOlder = reject
        }),
      )
      .mockResolvedValueOnce([makeWorkspace({ id: 'ws-a' })])

    act(() => {
      window.dispatchEvent(new Event('online'))
    })
    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.loadFailed).toBe(false)

    await act(async () => {
      rejectOlder(new Error('network'))
    })

    expect(result.current.loadFailed).toBe(false)
    expect(result.current.current?.id).toBe('ws-a')
    expect(result.current.isLoading).toBe(false)
  })
})

describe('selection', () => {
  it('selects the first workspace when nothing is stored', async () => {
    const first = makeWorkspace({ id: 'ws-a' })
    const second = makeWorkspace({ id: 'ws-b' })
    workspacesApi.list.mockResolvedValue([first, second])

    const { result } = await renderWorkspace()

    expect(result.current.current?.id).toBe('ws-a')
    expect(localStorage.getItem('workspace_id')).toBe('ws-a')
  })

  it('restores the stored workspace when it is still accessible', async () => {
    localStorage.setItem('workspace_id', 'ws-b')
    workspacesApi.list.mockResolvedValue([makeWorkspace({ id: 'ws-a' }), makeWorkspace({ id: 'ws-b', name: 'Family' })])

    const { result } = await renderWorkspace()

    expect(result.current.current?.name).toBe('Family')
  })

  it('falls back to the first when the stored workspace is gone', async () => {
    // Archived workspace, or the user was removed from it. Keeping the stale
    // id would send workspace_id headers the server rejects on every call.
    localStorage.setItem('workspace_id', 'ws-deleted')
    workspacesApi.list.mockResolvedValue([makeWorkspace({ id: 'ws-a' })])

    const { result } = await renderWorkspace()

    expect(result.current.current?.id).toBe('ws-a')
    expect(localStorage.getItem('workspace_id')).toBe('ws-a')
  })

  it('clears the stored id when the user has no workspaces at all', async () => {
    localStorage.setItem('workspace_id', 'ws-deleted')
    workspacesApi.list.mockResolvedValue([])

    const { result } = await renderWorkspace()

    expect(result.current.current).toBeNull()
    expect(localStorage.getItem('workspace_id')).toBeNull()
  })
})

describe('switchWorkspace', () => {
  /** Render against a client we hold, so cache effects are observable. */
  async function renderWithClient(queryClient = createTestQueryClient()) {
    function localWrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          <WorkspaceProvider>{children}</WorkspaceProvider>
        </QueryClientProvider>
      )
    }
    const rendered = renderHook(() => useWorkspace(), { wrapper: localWrapper })
    await waitFor(() => expect(rendered.result.current.isLoading).toBe(false))
    return { ...rendered, queryClient }
  }

  it('persists the new id before the refetches go out', async () => {
    // The axios interceptor reads workspace_id from localStorage, so writing
    // it after resetQueries would refetch the new workspace's screens with
    // the old workspace's header.
    workspacesApi.list.mockResolvedValue([makeWorkspace({ id: 'ws-a' }), makeWorkspace({ id: 'ws-b' })])

    const { result } = await renderWorkspace()

    await act(async () => {
      await result.current.switchWorkspace('ws-b')
    })

    expect(localStorage.getItem('workspace_id')).toBe('ws-b')
    expect(result.current.current?.id).toBe('ws-b')
  })

  it('drops the previous workspace data from the cache', async () => {
    // Every cached query was scoped to the workspace we just left. Leaving it
    // in place shows one workspace's figures under another's name.
    workspacesApi.list.mockResolvedValue([makeWorkspace({ id: 'ws-a' }), makeWorkspace({ id: 'ws-b' })])

    const { result, queryClient } = await renderWithClient()
    queryClient.setQueryData(['transactions'], [{ id: 't1', amount: 100 }])

    await act(async () => {
      await result.current.switchWorkspace('ws-b')
    })

    expect(queryClient.getQueryData(['transactions'])).toBeUndefined()
  })

  it('leaves the cache alone when switching to the workspace already active', async () => {
    // A no-op switch must not throw away data the user is looking at.
    workspacesApi.list.mockResolvedValue([makeWorkspace({ id: 'ws-a' })])

    const { result, queryClient } = await renderWithClient()
    queryClient.setQueryData(['transactions'], [{ id: 't1', amount: 100 }])

    await act(async () => {
      await result.current.switchWorkspace('ws-a')
    })

    expect(result.current.current?.id).toBe('ws-a')
    expect(queryClient.getQueryData(['transactions'])).toBeDefined()
  })
})

describe('roles', () => {
  const cases: Array<{
    role: WorkspaceRole
    canManage: boolean
    canWrite: boolean
  }> = [
    { role: 'owner', canManage: true, canWrite: true },
    { role: 'manager', canManage: true, canWrite: true },
    { role: 'editor', canManage: false, canWrite: true },
    { role: 'viewer', canManage: false, canWrite: false },
  ]

  for (const { role, canManage, canWrite } of cases) {
    it(`grants ${role} canManage=${canManage} canWrite=${canWrite}`, async () => {
      workspacesApi.list.mockResolvedValue([makeWorkspace({ role })])

      const { result } = await renderWorkspace()

      expect(result.current.role).toBe(role)
      expect(result.current.canManage).toBe(canManage)
      expect(result.current.canWrite).toBe(canWrite)
    })
  }

  it('grants nothing when there is no active workspace', async () => {
    workspacesApi.list.mockResolvedValue([])

    const { result } = await renderWorkspace()

    expect(result.current.role).toBeNull()
    expect(result.current.canManage).toBe(false)
    expect(result.current.canWrite).toBe(false)
  })
})

describe('hasModule', () => {
  it('is true only for modules the server enabled', async () => {
    workspacesApi.list.mockResolvedValue([makeWorkspace({ enabled_modules: ['transactions', 'budgets'] })])

    const { result } = await renderWorkspace()

    expect(result.current.hasModule('transactions')).toBe(true)
    expect(result.current.hasModule('budgets')).toBe(true)
    expect(result.current.hasModule('invoices')).toBe(false)
  })

  it('is false for everything when the workspace enables nothing', async () => {
    workspacesApi.list.mockResolvedValue([makeWorkspace({ enabled_modules: [] })])

    const { result } = await renderWorkspace()

    expect(result.current.enabledModules).toEqual([])
    expect(result.current.hasModule('transactions')).toBe(false)
  })

  it('is false when there is no active workspace', async () => {
    workspacesApi.list.mockResolvedValue([])

    const { result } = await renderWorkspace()

    expect(result.current.hasModule('transactions')).toBe(false)
  })
})

describe('useWorkspace', () => {
  it('refuses to be used outside the provider', () => {
    expect(() => renderHook(() => useWorkspace())).toThrow('useWorkspace must be used within a WorkspaceProvider')
  })
})
