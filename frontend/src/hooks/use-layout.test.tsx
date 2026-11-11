import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { useLayout } from '@/hooks/use-layout'
import { createTestQueryClient } from '@/test/utils'

const server = vi.hoisted(() => ({
  // While true, every request fails, as if the server were down.
  down: false,
  saved: {} as Record<string, { id: string }[] | null>,
  // A save to a surface listed here waits until it's released.
  holds: {} as Record<string, Promise<void>>,
}))

vi.mock('@/lib/api', () => ({
  uiLayouts: {
    get: async () => {
      if (server.down) throw new Error('down')
      return { dashboard: null, reports: null, ...server.saved }
    },
    save: async (surface: string, items: { id: string }[]) => {
      await server.holds[surface]
      if (server.down) throw new Error('down')
      server.saved = { ...server.saved, [surface]: items }
    },
    reset: async (surface: string) => {
      server.saved = { ...server.saved, [surface]: null }
    },
  },
}))

beforeEach(() => {
  server.down = false
  server.saved = {}
  server.holds = {}
})

function renderBoth() {
  const client = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return renderHook(() => ({ dashboard: useLayout('dashboard'), reports: useLayout('reports') }), { wrapper })
}

describe('useLayout', () => {
  it("doesn't let one surface's save bring back the other's old arrangement", async () => {
    const { result } = renderBoth()
    await waitFor(() => expect(result.current.reports.ready).toBe(true))

    let release: () => void = () => {}
    server.holds.reports = new Promise((done) => { release = done })
    act(() => result.current.reports.save([{ id: 'net_worth' }]))
    act(() => result.current.dashboard.save([{ id: 'overview' }]))

    // The dashboard save landed and was read back; the pending report choice stays.
    await waitFor(() => expect(server.saved.dashboard).toEqual([{ id: 'overview' }]))
    await new Promise((done) => setTimeout(done, 20))
    expect(result.current.reports.ids).toEqual(['net_worth'])
    expect(result.current.dashboard.ids).toEqual(['overview'])

    release()
    await waitFor(() => expect(server.saved.reports).toEqual([{ id: 'net_worth' }]))
    expect(result.current.reports.ids).toEqual(['net_worth'])
  })

  it('sends saves to a surface in the order they were made', async () => {
    const { result } = renderBoth()
    await waitFor(() => expect(result.current.dashboard.ready).toBe(true))
    let release: () => void = () => {}
    server.holds.dashboard = new Promise((done) => { release = done })

    act(() => result.current.dashboard.save([{ id: 'goals' }]))
    act(() => result.current.dashboard.save([{ id: 'overview' }, { id: 'goals', span: 4 }]))
    release()

    await waitFor(() => expect(server.saved.dashboard).toEqual([{ id: 'overview' }, { id: 'goals', span: 4 }]))
    expect(result.current.dashboard.ids).toEqual(['overview', 'goals'])
  })

  it('puts back the last arrangement when a save fails and the server can\'t be read either', async () => {
    server.saved = { dashboard: [{ id: 'goals' }] }
    const { result } = renderBoth()
    await waitFor(() => expect(result.current.dashboard.ids).toEqual(['goals']))

    let release: () => void = () => {}
    server.holds.dashboard = new Promise((done) => { release = done })
    act(() => result.current.dashboard.save([{ id: 'overview' }]))
    await waitFor(() => expect(result.current.dashboard.ids).toEqual(['overview']))

    server.down = true
    release()
    await waitFor(() => expect(result.current.dashboard.ids).toEqual(['goals']))
  })
})
