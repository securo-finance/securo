import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import { TransactionDrillDown } from '@/components/transaction-drill-down'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { admin, dashboard, transactions } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
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
})
