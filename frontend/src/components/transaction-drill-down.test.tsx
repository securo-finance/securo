import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import { TransactionDrillDown } from '@/components/transaction-drill-down'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { admin, dashboard, transactions } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'

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

  it('preserves the expense share of recurring category splits in the Overview panel', async () => {
    vi.mocked(dashboard.projectedTransactions).mockResolvedValue([{
      recurring_id: 'split-bill', account_id: 'account-1', description: 'Split recurring bill',
      amount: 100, amount_primary: 200, currency: 'EUR', type: 'debit', date: '2026-11-10',
      category_id: null, has_category_lines: true, category_lines: [
        { category_id: 'food', category_name: 'Food', category_icon: null, category_color: null, amount: 80, treat_as_transfer: false },
        { category_id: 'investment', category_name: 'Investment', category_icon: null, category_color: null, amount: 20, treat_as_transfer: true },
      ],
    }] as never)
    renderWithProviders(<TransactionDrillDown filter={{ title: 'Expenses', type: 'debit', from: '2026-11-01', to: '2026-11-30' }} onClose={vi.fn()} />)
    expect(await screen.findByText('Split recurring bill')).toBeInTheDocument()
    expect(screen.getByText('-€80.00')).toBeInTheDocument()
    expect(screen.getAllByText('$160.00').length).toBeGreaterThan(0)
    expect(screen.queryByText('$200.00')).not.toBeInTheDocument()
    expect(screen.getByText(/part of €100.00/)).toBeInTheDocument()
  })

  describe('quick categorize', () => {
    const groceries = { id: 'cat-groceries', name: 'Groceries', icon: null, color: '#10b981', group_id: null }
    const row = (overrides: Record<string, unknown>) => ({
      id: 'tx', description: 'Corner shop', date: '2026-09-05', type: 'debit', amount: 12,
      amount_primary: 12, currency: 'USD', status: 'posted', category: null, category_id: null,
      attachment_count: 0, splits: [], viewer_share: null, is_shared: false, user_id: 'me', ...overrides,
    })

    function renderQuick(items: Record<string, unknown>[], withQuick = true) {
      vi.mocked(transactions.list).mockResolvedValue({ items, total: items.length } as never)
      const onCategorize = vi.fn().mockResolvedValue(undefined)
      const onTransactionClick = vi.fn()
      const onClose = vi.fn()
      const utils = renderWithProviders(
        <TransactionDrillDown
          filter={filter}
          onClose={onClose}
          onTransactionClick={onTransactionClick}
          quickCategorize={withQuick
            ? { categories: [groceries] as never, groups: [], onCategorize }
            : undefined}
        />,
      )
      return { ...utils, onCategorize, onTransactionClick, onClose }
    }

    it('categorizes a row in place without opening its dialog', async () => {
      const { user, onCategorize, onTransactionClick } = renderQuick([row({ id: 'shop' })])

      await user.click(await screen.findByRole('button', { name: t('dashboard.drillDownQuickCategorize') }))
      await user.click(await screen.findByText('Groceries'))

      await waitFor(() => expect(onCategorize).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'shop' }), 'cat-groceries',
      ))
      expect(onTransactionClick).not.toHaveBeenCalled()
    })

    it('offers the picker only on own, unsplit, uncategorized rows', async () => {
      renderQuick([
        row({ id: 'plain', description: 'Plain row' }),
        row({ id: 'shared', description: 'Shared row', is_shared: true }),
        row({ id: 'done', description: 'Done row', category_id: 'cat-groceries', category: groceries }),
      ])

      expect(await screen.findByText('Shared row')).toBeInTheDocument()
      expect(screen.getAllByRole('button', { name: t('dashboard.drillDownQuickCategorize') })).toHaveLength(1)
    })

    it('opens a row from its button and hands over the rows to step through', async () => {
      const { user, onTransactionClick } = renderQuick([
        row({ id: 'first', description: 'First row', date: '2026-09-01' }),
        row({ id: 'second', description: 'Second row', date: '2026-09-02' }),
      ])

      const open = await screen.findAllByRole('button', { name: t('dashboard.drillDownOpenTransaction') })
      await user.click(open[0])

      expect(onTransactionClick).toHaveBeenCalledOnce()
      const [tx, queue] = onTransactionClick.mock.calls[0]
      expect(tx.id).toBe('first')
      expect(queue.map((q: { id: string }) => q.id)).toEqual(['first', 'second'])
    })

    it('does not open a row while its category is being saved', async () => {
      const { user, onCategorize, onTransactionClick } = renderQuick([row({ id: 'shop' })])
      onCategorize.mockReturnValue(new Promise(() => {}))

      await user.click(await screen.findByRole('button', { name: t('dashboard.drillDownQuickCategorize') }))
      await user.click(await screen.findByText('Groceries'))
      await waitFor(() => expect(onCategorize).toHaveBeenCalledOnce())

      fireEvent.click(screen.getByRole('button', { name: t('dashboard.drillDownOpenTransaction') }))
      fireEvent.click(screen.getByText('Corner shop'))

      expect(onTransactionClick).not.toHaveBeenCalled()
    })

    it('still closes the panel on Escape from a closed picker', async () => {
      const { onClose } = renderQuick([row({ id: 'shop' })])

      const trigger = await screen.findByRole('button', { name: t('dashboard.drillDownQuickCategorize') })
      trigger.focus()
      fireEvent.keyDown(trigger, { key: 'Escape' })

      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    })

    it('shows no picker unless the host opts in', async () => {
      renderQuick([row({ id: 'plain' })], false)

      expect(await screen.findByText('Corner shop')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: t('dashboard.drillDownQuickCategorize') })).not.toBeInTheDocument()
    })
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

  it('counts the owner share of a split credit only when asked, and shows the full amount', async () => {
    vi.mocked(transactions.list).mockResolvedValue({
      items: [{
        id: 'owned', description: 'Group refund', date: '2026-11-05', type: 'credit', amount: 100,
        amount_primary: 100, currency: 'USD', status: 'posted', category: null, attachment_count: 0,
        splits: [{ share_amount: 50 }, { share_amount: 50 }], viewer_share: 50, is_shared: false, user_id: 'me',
      }],
      total: 1,
    } as never)
    const base = { title: 'Income', type: 'credit' as const, from: '2026-11-01', to: '2026-11-30', ownShare: true }

    const { unmount } = renderWithProviders(<TransactionDrillDown filter={base} onClose={vi.fn()} />)
    // The statement counts credits in full.
    expect((await screen.findAllByText('$100.00')).length).toBeGreaterThan(0)
    expect(screen.queryByText(/part of/)).not.toBeInTheDocument()
    unmount()

    renderWithProviders(<TransactionDrillDown filter={{ ...base, ownShareCredits: true }} onClose={vi.fn()} />)
    expect(await screen.findByText('+$50.00')).toBeInTheDocument()
    expect(screen.getByText(/part of \$100\.00/)).toBeInTheDocument()
  })
})
