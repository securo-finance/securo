import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import { TransactionDrillDown } from '@/components/transaction-drill-down'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { admin, dashboard, transactions } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', async (load) => ({
  ...await load<typeof import('@/contexts/workspace-context')>(),
  useWorkspace: () => ({ canWrite: true }),
}))

vi.mock('@/lib/api', () => ({
  transactions: { list: vi.fn(), setReview: vi.fn() },
  dashboard: { projectedTransactions: vi.fn() },
  admin: { accountingMode: vi.fn() },
}))

const filter = { title: 'Uncategorized', uncategorized: true }

// Minimal row for PR-2 tests: every field the panel reads plus the
// review/flag axis.
function makeTx(overrides: Record<string, unknown> = {}) {
  return {
    id: 'tx-1',
    user_id: 'user-1',
    account_id: 'acc-1',
    category_id: null,
    category: null,
    external_id: null,
    description: 'Coffee shop',
    original_description: null,
    amount: -4.5,
    currency: 'USD',
    date: '2026-03-02',
    type: 'debit',
    source: 'csv',
    status: 'posted',
    payee: null,
    payee_id: null,
    payee_name: null,
    notes: null,
    transfer_pair_id: null,
    amount_primary: -4.5,
    fx_rate_used: null,
    fx_fallback: false,
    installment_number: null,
    total_installments: null,
    installment_total_amount: null,
    installment_purchase_date: null,
    installment_series_id: null,
    bill_id: null,
    effective_bill_date: null,
    splits: [],
    is_ignored: false,
    review_status: 'needs_review',
    reviewed_at: null,
    reviewed_by_id: null,
    review_attribution: null,
    flagged: false,
    ...overrides,
  }
}

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

  async function renderWithRow(row: Record<string, unknown>, { dialogOpen = false } = {}) {
    vi.mocked(transactions.list).mockResolvedValue({
      items: [row],
      total: 1,
      page: 1,
      limit: 50,
    } as never)
    const { onClose } = renderPanel({ dialogOpen })
    const description = await screen.findByText(String(row.description))
    const rowEl = description.closest('div[title]') as HTMLElement
    expect(rowEl).not.toBeNull()
    return { rowEl, onClose }
  }

  it.each([
    {
      caseId: 'drill-reviewed-human',
      label: 'Reviewed by human',
      attribution: {
        kind: 'human',
        reviewed_at: '2026-10-15T12:30:00Z',
        user_id: '11111111-1111-4111-8111-111111111111',
      },
      reviewedById: '11111111-1111-4111-8111-111111111111',
      omitAttribution: false,
    },
    {
      caseId: 'drill-reviewed-rule',
      label: 'Reviewed by rule',
      attribution: {
        kind: 'rule',
        reviewed_at: '2026-10-15T12:30:00Z',
        rule_id: '22222222-2222-4222-8222-222222222222',
      },
      reviewedById: null,
      omitAttribution: false,
    },
    {
      caseId: 'drill-reviewed-unknown-paired-stamps',
      label: 'Reviewed (unattributed)',
      attribution: { kind: 'unknown' },
      reviewedById: '11111111-1111-4111-8111-111111111111',
      omitAttribution: false,
    },
    {
      caseId: 'drill-reviewed-v1-missing-attribution',
      label: 'Reviewed (unattributed)',
      attribution: null,
      reviewedById: '11111111-1111-4111-8111-111111111111',
      omitAttribution: true,
    },
  ])('$caseId', async ({ caseId, label, attribution, reviewedById, omitAttribution }) => {
    const row: Record<string, unknown> = makeTx({
      description: caseId,
      review_status: 'reviewed',
      reviewed_at: '2026-10-15T12:30:00Z',
      reviewed_by_id: reviewedById,
      review_attribution: attribution,
    })
    // V1 omitted the new field; paired old stamps do not prove a human.
    if (omitAttribution) delete row.review_attribution
    const { rowEl } = await renderWithRow(row)
    const rowScope = within(rowEl)
    expect(rowScope.getByText(caseId)).toBeInTheDocument()

    expect(rowScope.queryByRole('img', { name: 'Needs review' })).not.toBeInTheDocument()
    for (const otherLabel of ['Reviewed by human', 'Reviewed by rule', 'Reviewed (unattributed)']) {
      if (otherLabel !== label) {
        expect(rowScope.queryByRole('img', { name: otherLabel })).not.toBeInTheDocument()
      }
    }
    expect(transactions.setReview).not.toHaveBeenCalled()
    // This renderer's API mock intentionally exposes no bulkReview endpoint.
    expect('bulkReview' in transactions).toBe(false)
    // Intended RED is this missing accessible badge, after the real row loaded.
    expect(rowScope.getByRole('img', { name: label })).toBeInTheDocument()
  })

  it('test: shift+r reviews active row', async () => {
    vi.mocked(transactions.setReview).mockResolvedValue(
      makeTx({ id: 'tx-1', review_status: 'reviewed', flagged: true }) as never,
    )
    const { rowEl } = await renderWithRow(makeTx({ flagged: true }))

    // The indicator is there before the shortcut fires...
    expect(
      screen.getByRole('img', { name: 'Needs review' }),
    ).toBeInTheDocument()

    fireEvent.mouseEnter(rowEl)
    fireEvent.keyDown(document, { key: 'R', shiftKey: true })

    // ...and the endpoint gets the forward transition for this row.
    await waitFor(() =>
      expect(transactions.setReview).toHaveBeenCalledWith('tx-1', 'reviewed'),
    )
    // The panel cache patches in the response: indicator gone, flag kept.
    await waitFor(() =>
      expect(
        screen.queryByRole('img', { name: 'Needs review' }),
      ).not.toBeInTheDocument(),
    )
    expect(screen.getByLabelText('Flagged')).toBeInTheDocument()
  })

  it('test: shift+r no-op without active row', async () => {
    await renderWithRow(makeTx({ flagged: false }))

    // Never hovered: no active row, so the shortcut has no target.
    fireEvent.keyDown(document, { key: 'R', shiftKey: true })

    expect(transactions.setReview).not.toHaveBeenCalled()
  })

  it('test: shift+r inert while a dialog is open', async () => {
    const { rowEl } = await renderWithRow(makeTx(), { dialogOpen: true })
    await screen.findByRole('dialog')

    fireEvent.mouseEnter(rowEl)
    fireEvent.keyDown(document, { key: 'R', shiftKey: true })

    expect(transactions.setReview).not.toHaveBeenCalled()
  })

  it('test: row shows needs-review indicator and flag star', async () => {
    vi.mocked(transactions.list).mockResolvedValue({
      items: [
        makeTx({ id: 'tx-1', description: 'Needs one', review_status: 'needs_review', flagged: true }),
        makeTx({ id: 'tx-2', description: 'Done one', review_status: 'reviewed', flagged: false }),
      ],
      total: 2,
      page: 1,
      limit: 50,
    } as never)
    renderPanel({ dialogOpen: false })
    await screen.findByText('Needs one')

    // Exactly one indicator — on the needs-review row only.
    expect(
      screen.getAllByRole('img', { name: 'Needs review' }),
    ).toHaveLength(1)
    // The flag star rides the flagged row; the reviewed row has neither.
    expect(screen.getByLabelText('Flagged')).toBeInTheDocument()
  })
})
