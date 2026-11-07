import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'

import { useCreateTransaction } from '@/hooks/use-create-transaction'
import { createTestQueryClient } from '@/test/utils'
import type { TransactionEditPayload } from '@/types'

const api = vi.hoisted(() => ({
  transactions: {
    create: vi.fn(),
    createInstallments: vi.fn(),
    attachments: { upload: vi.fn() },
  },
  recurring: { create: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))

const tx: TransactionEditPayload = {
  description: 'Coffee',
  amount: 12.5,
  type: 'debit',
  date: '2026-09-23',
  account_id: 'acc-1',
  category_id: 'cat-1',
}

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
  queryClient = createTestQueryClient()
  api.transactions.create.mockResolvedValue({ id: 'tx-1' })
  api.transactions.createInstallments.mockResolvedValue([{ id: 'tx-a' }, { id: 'tx-b' }])
  api.transactions.attachments.upload.mockResolvedValue({})
  api.recurring.create.mockResolvedValue({})
})

describe('useCreateTransaction', () => {
  it('creates the transaction, uploads pending files and closes on a plain save', async () => {
    const onDone = vi.fn()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const file = new File(['x'], 'receipt.pdf')
    const { result } = renderHook(() => useCreateTransaction({ onDone }), { wrapper })

    act(() => result.current.create(tx, undefined, undefined, [file], 'save'))

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(api.transactions.create).toHaveBeenCalledWith(tx)
    expect(api.transactions.attachments.upload).toHaveBeenCalledWith('tx-1', file)
    expect(api.recurring.create).not.toHaveBeenCalled()
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey?.[0])
    expect(keys).toEqual(expect.arrayContaining(['transactions', 'accounts', 'dashboard', 'recurring']))
  })

  it('creates a recurring entry that skips the first occurrence', async () => {
    const onDone = vi.fn()
    const { result } = renderHook(() => useCreateTransaction({ onDone }), { wrapper })

    act(() => result.current.create(tx, { frequency: 'monthly' }))

    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(api.recurring.create).toHaveBeenCalledWith(expect.objectContaining({
      description: 'Coffee',
      currency: 'BRL',
      frequency: 'monthly',
      start_date: '2026-09-23',
      account_id: 'acc-1',
      skip_first: true,
    }))
  })

  it('creates an installment series and attaches files to its first row', async () => {
    const file = new File(['x'], 'receipt.pdf')
    const installments = { base: tx, total_installments: 3 } as never
    const { result } = renderHook(() => useCreateTransaction({ onDone: vi.fn() }), { wrapper })

    act(() => result.current.create(tx, undefined, installments, [file]))

    await waitFor(() => expect(api.transactions.attachments.upload).toHaveBeenCalled())
    expect(api.transactions.createInstallments).toHaveBeenCalledWith(installments)
    expect(api.transactions.create).not.toHaveBeenCalled()
    expect(api.transactions.attachments.upload).toHaveBeenCalledWith('tx-a', file)
  })

  it('keeps the dialog open and reseeds the form on save and duplicate', async () => {
    const onDone = vi.fn()
    const { result } = renderHook(() => useCreateTransaction({ onDone }), { wrapper })
    const initialKey = result.current.formResetKey

    act(() => result.current.create(tx, undefined, undefined, undefined, 'saveAndDuplicate'))

    await waitFor(() => expect(result.current.duplicateDraft).toEqual(tx))
    expect(result.current.formResetKey).toBe(initialKey + 1)
    expect(onDone).not.toHaveBeenCalled()
  })

  it('keeps the dialog open with an empty form on save and new', async () => {
    const onDone = vi.fn()
    const { result } = renderHook(() => useCreateTransaction({ onDone }), { wrapper })
    act(() => result.current.resetForm(tx))

    act(() => result.current.create(tx, undefined, undefined, undefined, 'saveAndNew'))

    await waitFor(() => expect(result.current.duplicateDraft).toBeNull())
    expect(result.current.formResetKey).toBe(2)
    expect(onDone).not.toHaveBeenCalled()
  })
})
