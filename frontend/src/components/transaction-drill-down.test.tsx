import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import { TransactionDrillDown } from '@/components/transaction-drill-down'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { admin, dashboard, transactions } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: 'me', preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/lib/api', () => ({
  transactions: { list: vi.fn() },
  dashboard: { projectedTransactions: vi.fn() },
  admin: { accountingMode: vi.fn() },
}))

const filter = { title: 'Uncategorized', uncategorized: true }

function renderPanel({ dialogOpen }: { dialogOpen: boolean }) {
  const onClose = vi.fn()
  const result = renderWithProviders(
    <>
      <TransactionDrillDown filter={filter} onClose={onClose} />
      <Dialog open={dialogOpen}>
        <DialogContent>
          <DialogTitle>Edit transaction</DialogTitle>
          <button type="button">Category</button>
        </DialogContent>
      </Dialog>
    </>,
  )
  return { ...result, onClose }
}

// The outside-click listener is attached after a short delay so the click
// that opened the panel does not close it.
async function waitForOutsideClickListener() {
  await new Promise(resolve => setTimeout(resolve, 150))
}

describe('TransactionDrillDown', () => {
  beforeEach(() => {
    vi.mocked(transactions.list).mockResolvedValue({ items: [], total: 0 } as never)
    vi.mocked(dashboard.projectedTransactions).mockResolvedValue([])
    vi.mocked(admin.accountingMode).mockResolvedValue({ mode: 'cash' } as never)
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  it('closes on a click outside the panel', async () => {
    const { onClose } = renderPanel({ dialogOpen: false })
    await waitForOutsideClickListener()

    fireEvent.mouseDown(document.body)

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('stays open while the user clicks inside a dialog opened from it', async () => {
    const { onClose } = renderPanel({ dialogOpen: true })
    await waitForOutsideClickListener()

    fireEvent.mouseDown(await screen.findByRole('button', { name: 'Category' }))

    expect(onClose).not.toHaveBeenCalled()
  })

  it('leaves Escape to the open dialog', async () => {
    const { onClose } = renderPanel({ dialogOpen: true })
    await screen.findByRole('dialog')

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes on Escape when no dialog is open', async () => {
    const { onClose } = renderPanel({ dialogOpen: false })

    fireEvent.keyDown(document, { key: 'Escape' })

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('totals only the user share of a split expense and asks for posted rows', async () => {
    const tx = (overrides: Record<string, unknown>) => ({
      id: 'tx', description: 'Dinner', date: '2026-09-05', type: 'debit', amount: 100,
      amount_primary: 100, currency: 'USD', status: 'posted', category: null,
      attachment_count: 0, splits: [], viewer_share: null, is_shared: false, user_id: 'me', ...overrides,
    })
    vi.mocked(transactions.list).mockResolvedValue({
      items: [
        // Owner of a 50/50 split keeps their half.
        tx({ id: 'owned', splits: [{ share_amount: 50 }, { share_amount: 50 }], viewer_share: 50 }),
        // A member sees only their own share of someone else's expense.
        tx({ id: 'shared', amount: 80, amount_primary: 80, is_shared: true, viewer_share: 20 }),
        tx({ id: 'refund', type: 'credit', amount: 10, amount_primary: 10 }),
        // Another member's split the user is not part of counts in full.
        tx({ id: 'other', amount: 30, amount_primary: 30, user_id: 'other', splits: [{ share_amount: 15 }] }),
      ],
      total: 4,
    } as never)

    renderWithProviders(
      <TransactionDrillDown
        filter={{ title: 'Food', from: '2026-09-01', to: '2026-09-12', realizedOnly: true, ownShare: true, netTotal: true }}
        onClose={vi.fn()}
      />,
    )

    expect((await screen.findAllByText('-$90.00')).length).toBeGreaterThan(0)
    expect(transactions.list).toHaveBeenCalledWith(expect.objectContaining({ status: 'posted', to: '2026-09-12' }))
  })
})
