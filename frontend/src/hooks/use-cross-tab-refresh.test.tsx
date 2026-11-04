import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { useCrossTabRefresh } from '@/hooks/use-cross-tab-refresh'
import { DATA_CHANGED_KEY } from '@/lib/data-changed'

function wrapper({ children }: { children: ReactNode }) {
  // staleTime 0 + no retry: only the invalidation this test triggers can
  // refetch the query, which is exactly what is under test.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

/** Dispatch what other tabs produce when they write the signal key. */
function fireStorageEvent(key: string | null) {
  const evt = new Event('storage') as Event & { key: string | null }
  Object.defineProperty(evt, 'key', { value: key })
  window.dispatchEvent(evt)
}

function renderSubject(fetchFn: () => Promise<string>) {
  return renderHook(
    () => {
      const query = useQuery({ queryKey: ['transactions'], queryFn: fetchFn })
      useCrossTabRefresh()
      return query
    },
    { wrapper },
  )
}

describe('useCrossTabRefresh', () => {
  beforeEach(() => localStorage.clear())

  it('refetches active queries when another tab changes data', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce('stale')
      .mockResolvedValue('fresh')
    const { result, unmount } = renderSubject(fetchFn)

    await waitFor(() => expect(result.current.data).toBe('stale'))

    fireStorageEvent(DATA_CHANGED_KEY)

    await waitFor(() => expect(result.current.data).toBe('fresh'))
    unmount()
  })

  it('ignores storage events for other keys', async () => {
    const fetchFn = vi.fn().mockResolvedValue('same')
    const { result, unmount } = renderSubject(fetchFn)

    await waitFor(() => expect(result.current.data).toBe('same'))

    fireStorageEvent('token')

    // A refetch would raise the call count; give it a moment to (not) happen.
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(fetchFn).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('stops listening after unmount', async () => {
    const fetchFn = vi.fn().mockResolvedValue('same')
    const { result, unmount } = renderSubject(fetchFn)

    await waitFor(() => expect(result.current.data).toBe('same'))
    unmount()

    fireStorageEvent(DATA_CHANGED_KEY)
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(fetchFn).toHaveBeenCalledTimes(1)
  })
})
