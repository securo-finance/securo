/**
 * "Scan for transfers" on the Rules page.
 *
 * The scan is the one-time backfill that pairs transfer rows imported before
 * the detector ran on arrival. It covers the whole history, so on a large
 * workspace it is one long request: the action asks before running, keeps the
 * button in a busy state while it runs, and reports how many pairs it made.
 * Viewers read rules; they never run a scan.
 */
import { createContext } from 'react'
import { useQuery } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import RulesPage from '@/pages/rules'
import { renderWithProviders, t } from '@/test/utils'
import type { ReconciliationSuggestion } from '@/types'

const api = vi.hoisted(() => ({
  rules: {
    list: vi.fn(),
    packs: vi.fn(),
    applyAll: vi.fn(),
  },
  categories: {
    listIncludingHidden: vi.fn(),
  },
  categoryGroups: {
    list: vi.fn(),
  },
  accounts: {
    list: vi.fn(),
  },
  payees: {
    list: vi.fn(),
  },
  connections: {
    detectTransfers: vi.fn(),
  },
  reconciliation: {
    suggestions: vi.fn(),
    history: vi.fn(),
    rules: vi.fn(),
  },
}))

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}))

const workspace = vi.hoisted(() => ({ canWrite: true }))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '***' }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: workspace.canWrite }),
  WorkspaceContext: createContext<{ canWrite: boolean } | undefined>({ canWrite: true }),
}))

beforeEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  workspace.canWrite = true
  api.rules.list.mockResolvedValue([])
  api.rules.packs.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
  api.connections.detectTransfers.mockResolvedValue({ pairs_created: 0 })
  api.reconciliation.suggestions.mockResolvedValue([])
  api.reconciliation.history.mockResolvedValue([])
  api.reconciliation.rules.mockResolvedValue([])
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

async function clickScan() {
  const button = await screen.findByRole('button', { name: t('rules.scanForTransfers') })
  fireEvent.click(button)
  return button
}

/** One ambiguous pair waiting in the queue: the scan writes these even when
 *  it creates no pair, and the count on the tab is how the reader learns it. */
function pendingSuggestion(): ReconciliationSuggestion {
  return {
    id: 'suggestion-1',
    node: 'transaction:debit-1',
    strategy_id: 'transfer_pair',
    expectation_kind: 'transaction',
    expectation_id: 'debit-1',
    expectation_label: 'Conta corrente',
    amount: '321.45',
    scores: { currency: 'BRL', amount_exact: true, days_apart: 0 },
    status: 'pending',
    created_at: '2026-11-20T12:00:00Z',
    covers: [
      {
        expectation_kind: 'transaction',
        expectation_id: 'debit-1',
        label: 'Conta corrente',
        amount: '321.45',
      },
    ],
    transaction: {
      id: 'credit-1',
      description: 'Transferência recebida',
      amount: '321.45',
      currency: 'BRL',
      date: '2026-11-20',
      type: 'credit',
      account_id: 'account-1',
    },
  }
}

describe('the transfer scan on the rules page', () => {
  it('asks before running and creates nothing when the user backs out', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderWithProviders(<RulesPage />)

    await clickScan()

    expect(window.confirm).toHaveBeenCalledWith(t('rules.confirmScanForTransfers'))
    expect(api.connections.detectTransfers).not.toHaveBeenCalled()
  })

  it('runs the scan once confirmed and reports how many pairs it created', async () => {
    api.connections.detectTransfers.mockResolvedValue({ pairs_created: 3 })
    renderWithProviders(<RulesPage />)

    await clickScan()

    await waitFor(() => expect(api.connections.detectTransfers).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(t('rules.transferScanCreated', { count: 3 })),
    )
  })

  it('says so when the scan found nothing to pair', async () => {
    api.connections.detectTransfers.mockResolvedValue({ pairs_created: 0 })
    renderWithProviders(<RulesPage />)

    await clickScan()

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(t('rules.transferScanNone')),
    )
  })

  it('refreshes the reconciliation queue and history after the scan', async () => {
    // No pair was certain, so the toast says nothing found, but the scan
    // still wrote every ambiguous pair to the queue the tab counts, and each
    // question writes a "suggested" event to the history.
    api.connections.detectTransfers.mockResolvedValue({ pairs_created: 0 })
    // Empty on the first read and one question waiting on the refetch: the
    // badge on the tab is what has to change, not only the number of calls.
    api.reconciliation.suggestions
      .mockResolvedValueOnce([])
      .mockResolvedValue([pendingSuggestion()])
    renderWithProviders(<RulesPage />)

    fireEvent.click(await screen.findByRole('button', { name: t('rules.tab.history') }))
    await waitFor(() => expect(api.reconciliation.history).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(api.reconciliation.suggestions).toHaveBeenCalledTimes(1))
    const queueTab = screen.getByTestId('automation-tab-queue')
    expect(within(queueTab).queryByText('1')).not.toBeInTheDocument()

    await clickScan()

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(t('rules.transferScanNone')),
    )
    await waitFor(() => expect(within(queueTab).getByText('1')).toBeInTheDocument())
    await waitFor(() => expect(api.reconciliation.history).toHaveBeenCalledTimes(2))
  })

  it('invalidates the money views so a linked pair shows without a reload', async () => {
    // A probe standing in for the transaction list: the scan links two rows
    // the person can already see, so the view has to be marked stale and
    // refetched on its own. Without the financial invalidation the rendered
    // rows stay as they were and this test fails.
    const moneyViews = vi
      .fn()
      .mockResolvedValueOnce(['debit-1'])
      .mockResolvedValue(['debit-1', 'credit-1'])
    function MoneyViewsProbe() {
      const { data } = useQuery<string[]>({ queryKey: ['transactions'], queryFn: moneyViews })
      return <span data-testid="money-rows">{(data ?? []).join(',')}</span>
    }
    renderWithProviders(
      <>
        <MoneyViewsProbe />
        <RulesPage />
      </>,
    )

    await waitFor(() => expect(screen.getByTestId('money-rows')).toHaveTextContent('debit-1'))
    expect(screen.getByTestId('money-rows')).not.toHaveTextContent('credit-1')

    await clickScan()

    await waitFor(() =>
      expect(screen.getByTestId('money-rows')).toHaveTextContent('debit-1,credit-1'),
    )
    expect(moneyViews).toHaveBeenCalledTimes(2)
  })

  it('keeps the button busy until the scan comes back', async () => {
    let resolve: (value: { pairs_created: number }) => void = () => {}
    api.connections.detectTransfers.mockReturnValue(
      new Promise<{ pairs_created: number }>((r) => {
        resolve = r
      }),
    )
    renderWithProviders(<RulesPage />)

    await clickScan()

    const busy = await screen.findByRole('button', { name: t('rules.scanningForTransfers') })
    expect(busy).toBeDisabled()

    resolve({ pairs_created: 1 })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: t('rules.scanForTransfers') })).toBeEnabled(),
    )
  })

  it('hides the action from a viewer', async () => {
    workspace.canWrite = false
    renderWithProviders(<RulesPage />)

    await screen.findByRole('combobox', { name: t('rules.filterByCategory') })

    expect(screen.queryByRole('button', { name: t('rules.scanForTransfers') })).not.toBeInTheDocument()
  })

  it('reports a failure instead of a count', async () => {
    api.connections.detectTransfers.mockRejectedValue(new Error('boom'))
    renderWithProviders(<RulesPage />)

    await clickScan()

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(toast.success).not.toHaveBeenCalled()
  })
})
