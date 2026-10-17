import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { EditDraftDialog, LinkPaymentDialog } from '@/pages/invoice-detail'
import { renderWithProviders, t } from '@/test/utils'
import type { Invoice, Transaction } from '@/types'

const api = vi.hoisted(() => ({
  invoices: { allocate: vi.fn(), deduct: vi.fn(), update: vi.fn() },
  payees: { list: vi.fn() },
  transactions: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  invoices: api.invoices,
  payees: api.payees,
  transactions: api.transactions,
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('sonner', () => ({ toast }))

const payment = {
  id: 'tx-1',
  description: 'Wire from Beta',
  amount: 1000,
  currency: 'USD',
  date: '2026-10-12',
  type: 'credit',
} as Transaction

describe('EditDraftDialog', () => {
  it('preserves the full catalog line when only the notes change', async () => {
    const line = {
      description: 'Design work',
      quantity: '2',
      unit: 'h',
      unit_price: '125.00',
      tax_rate: '10',
      product_id: 'product-1',
      price_id: 'price-1',
      fiscal_refs: { service_code: 'design' },
    }
    const invoice: Invoice = {
      id: 'draft-1',
      payee_id: null,
      payee: null,
      document_type: 'invoice',
      direction: 'receivable',
      origin: 'local',
      external_source: null,
      external_id: null,
      number: null,
      series: null,
      external_number: null,
      status: 'draft',
      state: 'draft',
      total: '275.00',
      subtotal: '250.00',
      discount: '0.00',
      tax_total: '25.00',
      amount_paid: '0.00',
      amount_deducted: '0.00',
      balance: '275.00',
      days_overdue: 0,
      next_due_date: null,
      currency: 'USD',
      issue_date: '2026-10-01',
      due_date: '2026-10-31',
      competence_date: null,
      sent_at: null,
      notes: null,
      internal_notes: null,
      custom_fields: null,
      snapshot: null,
      share_token: null,
      schedule_id: null,
      schedule: null,
      sequence: null,
      period_start: null,
      period_end: null,
      installments: [],
      deductions: [],
      allocations: [],
      lines: [{ ...line, id: 'line-1', total: '275.00', position: 0 }],
      created_at: '2026-10-01T00:00:00Z',
    }
    api.payees.list.mockResolvedValue([])
    api.invoices.update.mockResolvedValue(invoice)
    const onSaved = vi.fn()
    const { user } = renderWithProviders(
      <EditDraftDialog
        open
        onOpenChange={vi.fn()}
        invoice={invoice}
        showTax
        currency="USD"
        onSaved={onSaved}
      />,
    )

    await user.type(screen.getByTestId('edit-notes-input'), 'Thanks for your business')
    await user.click(screen.getByTestId('edit-submit'))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(api.invoices.update).toHaveBeenCalledWith('draft-1', {
      payee_id: null,
      due_date: '2026-10-31',
      notes: 'Thanks for your business',
      installments: [],
      lines: [line],
    })
  })
})

function apiError(code: string) {
  return { response: { data: { detail: { code } } } }
}

function renderDialog() {
  const onOpenChange = vi.fn()
  const onLinked = vi.fn()
  const { user } = renderWithProviders(
    <LinkPaymentDialog
      open
      onOpenChange={onOpenChange}
      invoiceId="inv-1"
      direction="receivable"
      balance="3000.00"
      settleTarget="1500.00"
      currency="USD"
      onLinked={onLinked}
    />,
  )
  return { user, onOpenChange, onLinked }
}

async function pickPaymentAndDifference(user: ReturnType<typeof renderDialog>['user']) {
  await user.click(await screen.findByTestId('invoice-candidate'))
  // 1500 expected, 1000 paid: the dialog offers to record the 500.
  expect(screen.getByTestId('invoice-difference')).toHaveTextContent('$500.00')
  await user.click(screen.getByTestId('invoice-difference-kind'))
  await user.click(screen.getByRole('option', { name: t('invoices.deductions.kind.withholding_tax') }))
}

describe('LinkPaymentDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.transactions.list.mockResolvedValue({ items: [payment], total: 1 })
  })

  it('links the payment and records the difference as a deduction', async () => {
    api.invoices.allocate.mockResolvedValue({})
    api.invoices.deduct.mockResolvedValue({})
    const { user, onOpenChange, onLinked } = renderDialog()
    await pickPaymentAndDifference(user)

    await user.click(screen.getByTestId('invoice-allocation-submit'))

    await waitFor(() => expect(onLinked).toHaveBeenCalled())
    expect(api.invoices.allocate).toHaveBeenCalledWith('inv-1', 'tx-1', undefined)
    expect(api.invoices.deduct).toHaveBeenCalledWith('inv-1', {
      kind: 'withholding_tax',
      amount: '500.00',
      transaction_id: 'tx-1',
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(toast.success).toHaveBeenCalledWith(t('invoices.linked'))
  })

  it('stays open when the link itself fails, so the person can retry', async () => {
    api.invoices.allocate.mockRejectedValue(apiError('over_allocation'))
    const { user, onOpenChange, onLinked } = renderDialog()
    await pickPaymentAndDifference(user)

    await user.click(screen.getByTestId('invoice-allocation-submit'))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(t('invoices.errors.over_allocation')))
    expect(api.invoices.deduct).not.toHaveBeenCalled()
    expect(onLinked).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('closes and refreshes when the payment linked but the deduction failed', async () => {
    // Left open, a second "link" would allocate the same payment twice.
    api.invoices.allocate.mockResolvedValue({})
    api.invoices.deduct.mockRejectedValue(apiError('over_allocation'))
    const { user, onOpenChange, onLinked } = renderDialog()
    await pickPaymentAndDifference(user)

    await user.click(screen.getByTestId('invoice-allocation-submit'))

    await waitFor(() => expect(onLinked).toHaveBeenCalled())
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(toast.error).toHaveBeenCalledWith(t('invoices.errors.over_allocation'))
    expect(toast.success).toHaveBeenCalledWith(t('invoices.linked'))
  })
})


describe('LinkPaymentDialog candidates', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('leaves out money already linked here or spent in full elsewhere', async () => {
    const link = (invoice_id: string, amount: string) => ({
      invoice_id, amount, number: 1, series: null, external_number: null,
    })
    api.transactions.list.mockResolvedValue({
      items: [
        { ...payment, id: 'here', description: 'Already on this invoice', invoice_links: [link('inv-1', '1000.00')] },
        { ...payment, id: 'spent', description: 'Spent on another', invoice_links: [link('inv-9', '1000.00')] },
        { ...payment, id: 'part', description: 'Half used elsewhere', invoice_links: [link('inv-9', '400.00')] },
        { ...payment, id: 'free', description: 'Untouched' },
      ],
      total: 4,
    })
    renderDialog()
    expect(await screen.findByText('Untouched')).toBeInTheDocument()
    expect(screen.getByText('Half used elsewhere')).toBeInTheDocument()
    expect(screen.queryByText('Already on this invoice')).not.toBeInTheDocument()
    expect(screen.queryByText('Spent on another')).not.toBeInTheDocument()
  })
})
