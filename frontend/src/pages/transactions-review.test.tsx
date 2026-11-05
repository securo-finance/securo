/**
 * PR-2 §8 page-level review workflow tests (named in the design packet).
 *
 * Renders the real TransactionsPage with a mocked API layer: the smart
 * chip count/filter toggle, the desktop filter-menu switch row, the row
 * indicator, and the bulk mark-reviewed flow (updates rows + clears
 * selection).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { CollectionFilterProvider } from '@/contexts/collection-filter-provider'
import { renderWithProviders, t } from '@/test/utils'

const feedback = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))
vi.mock('sonner', () => ({ toast: feedback }))
const platform = vi.hoisted(() => ({ isMobile: false, canWrite: true }))

vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => platform.isMobile,
}))

const api = vi.hoisted(() => ({
  transactions: {
    list: vi.fn(),
    calendar: vi.fn(),
    bulkReview: vi.fn(),
    bulkCategorize: vi.fn(),
    bulkAddTags: vi.fn(),
    bulkRemoveTags: vi.fn(),
    bulkDelete: vi.fn(),
    bulkAddToGroup: vi.fn(),
    update: vi.fn(),
    setReview: vi.fn(),
    export: vi.fn(),
  },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  recurring: { list: vi.fn() },
  payees: { list: vi.fn() },
  admin: { accountingMode: vi.fn() },
  groups: { list: vi.fn(), get: vi.fn() },
  rules: { create: vi.fn() },
  reconciliation: { suggestions: vi.fn() },
  collections: { list: vi.fn() },
  dashboard: { projectedTransactions: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: 'user-1', preferences: { currency_display: 'USD' } }, updateUser: vi.fn() }),
}))

vi.mock('@/contexts/workspace-context', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/contexts/workspace-context')>()
  return {
    ...original,
    useWorkspace: () => ({
      canWrite: platform.canWrite,
      canManage: true,
      hasModule: () => true,
      current: { id: 'ws-1', name: 'Test WS', role: 'owner' },
      workspaces: [],
      refresh: async () => {},
      switchWorkspace: async () => {},
    }),
  }
})

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

function makeTx(overrides: Record<string, unknown> = {}) {
  return {
    id: 'tx-1',
    user_id: 'user-1',
    account_id: 'acc-1',
    category_id: null,
    category: null,
    external_id: null,
    description: 'Needs one',
    original_description: null,
    amount: -12.34,
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
    amount_primary: -12.34,
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

function envelope(items: unknown[], needsReviewCount: number) {
  return {
    items,
    total: items.length,
    page: 1,
    limit: 50,
    summary: { income: 0, expense: 0, net: 0, excluded: 0, currency: 'USD' },
    needs_review_count: needsReviewCount,
  }
}

describe('transactions review workflow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    platform.isMobile = false
    platform.canWrite = true
    api.transactions.list.mockResolvedValue(envelope([makeTx()], 1))
    api.transactions.calendar.mockResolvedValue({ days: [] })
    api.transactions.bulkReview.mockResolvedValue({ updated: 1, matched: 1 })
    api.categories.list.mockResolvedValue([])
    api.categories.listIncludingHidden.mockResolvedValue([])
    api.categoryGroups.list.mockResolvedValue([])
    api.accounts.list.mockResolvedValue([
      { id: 'acc-1', user_id: 'user-1', name: 'Checking', type: 'checking', currency: 'USD', balance: 100, is_hidden: false },
    ])
    api.recurring.list.mockResolvedValue([])
    api.payees.list.mockResolvedValue([])
    api.admin.accountingMode.mockResolvedValue({ mode: 'cash' })
    api.groups.list.mockResolvedValue([])
    api.groups.get.mockResolvedValue(null)
    api.rules.create.mockResolvedValue({})
    api.reconciliation.suggestions.mockResolvedValue([])
    api.collections.list.mockResolvedValue([])
  })

  /** The page reads collection scope from its provider; the app mounts
   *  that provider in App.tsx, so tests mount it too. */
  function renderPage() {
    return renderWithProviders(
      <CollectionFilterProvider>
        <TransactionsPage />
      </CollectionFilterProvider>,
      { route: '/transactions' },
    )
  }

  // Prospective consumer coverage of the real mobile branch, not viewport/layout evidence.
  it.each([
    {
      caseId: 'page-mobile-reviewed-human',
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
      caseId: 'page-mobile-reviewed-rule',
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
      caseId: 'page-mobile-reviewed-unknown-paired-stamps',
      label: 'Reviewed (unattributed)',
      attribution: { kind: 'unknown' },
      reviewedById: '11111111-1111-4111-8111-111111111111',
      omitAttribution: false,
    },
    {
      caseId: 'page-mobile-reviewed-v1-missing-attribution',
      label: 'Reviewed (unattributed)',
      attribution: null,
      reviewedById: '11111111-1111-4111-8111-111111111111',
      omitAttribution: true,
    },
  ])('$caseId', async ({ caseId, label, attribution, reviewedById, omitAttribution }) => {
    platform.isMobile = true
    const row: Record<string, unknown> = makeTx({
      description: caseId,
      review_status: 'reviewed',
      reviewed_at: '2026-10-15T12:30:00Z',
      reviewed_by_id: reviewedById,
      review_attribution: attribution,
    })
    // V1 omitted the new field; paired old stamps do not prove a human.
    if (omitAttribution) delete row.review_attribution
    api.transactions.list.mockResolvedValue(envelope([row], 0))

    renderPage()
    const description = await screen.findByText(caseId)
    expect(description.tagName).toBe('P')
    expect(description.closest('tr')).toBeNull()
    // The real MobileTransactionRow root is a div.border-b card.
    const mobileCard = description.closest('div.border-b')
    expect(mobileCard).not.toBeNull()
    expect(mobileCard).toHaveClass('bg-card')
    const cardScope = within(mobileCard as HTMLElement)
    expect(cardScope.getByText(caseId)).toBeInTheDocument()
    expect(cardScope.getByRole('checkbox')).toBeInTheDocument()

    expect(cardScope.queryByRole('img', { name: 'Needs review' })).not.toBeInTheDocument()
    for (const otherLabel of ['Reviewed by human', 'Reviewed by rule', 'Reviewed (unattributed)']) {
      if (otherLabel !== label) {
        expect(cardScope.queryByRole('img', { name: otherLabel })).not.toBeInTheDocument()
      }
    }
    expect(api.transactions.setReview).not.toHaveBeenCalled()
    expect(api.transactions.bulkReview).not.toHaveBeenCalled()
    // Intended RED is this missing accessible badge, after the real mobile card loaded.
    expect(cardScope.getByRole('img', { name: label })).toBeInTheDocument()
  })

  it.each([
    {
      caseId: 'page-reviewed-human',
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
      caseId: 'page-reviewed-rule',
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
      caseId: 'page-reviewed-unknown-paired-stamps',
      label: 'Reviewed (unattributed)',
      attribution: { kind: 'unknown' },
      reviewedById: '11111111-1111-4111-8111-111111111111',
      omitAttribution: false,
    },
    {
      caseId: 'page-reviewed-v1-missing-attribution',
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
    api.transactions.list.mockResolvedValue(envelope([row], 0))

    renderPage()
    const description = await screen.findByText(caseId)
    const rowEl = description.closest('tr')
    expect(rowEl).not.toBeNull()
    const rowScope = within(rowEl as HTMLElement)
    expect(rowScope.getByText(caseId)).toBeInTheDocument()

    expect(rowScope.queryByRole('img', { name: 'Needs review' })).not.toBeInTheDocument()
    for (const otherLabel of ['Reviewed by human', 'Reviewed by rule', 'Reviewed (unattributed)']) {
      if (otherLabel !== label) {
        expect(rowScope.queryByRole('img', { name: otherLabel })).not.toBeInTheDocument()
      }
    }
    expect(api.transactions.setReview).not.toHaveBeenCalled()
    expect(api.transactions.bulkReview).not.toHaveBeenCalled()
    // Intended RED is this missing accessible badge, after the real row loaded.
    expect(rowScope.getByRole('img', { name: label })).toBeInTheDocument()
  })

  it('test: needs-review chip shows count and toggles filter', async () => {
    const { user } = renderPage()

    // N comes from the envelope: needs-review rows under current filters.
    const chip = await screen.findByRole('button', { name: /Needs review \(1\)/ })
    expect(chip).toHaveAttribute('aria-pressed', 'false')

    await user.click(chip)
    await waitFor(() =>
      expect(api.transactions.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ review_status: 'needs_review' }),
      ),
    )

    // Toggling back off drops the param again.
    await user.click(screen.getByRole('button', { name: /Needs review \(1\)/ }))
    await waitFor(() =>
      expect(api.transactions.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ review_status: undefined }),
      ),
    )
  })

  it('test: bulk mark reviewed updates rows and clears selection', async () => {
    const row = makeTx({ id: 'tx-1', description: 'Needs one' })
    api.transactions.list
      .mockResolvedValueOnce(envelope([row], 1))
      .mockResolvedValue(envelope([{ ...row, review_status: 'reviewed' }], 0))

    const { user } = renderPage()
    await screen.findByText('Needs one')
    // Row is in the inbox before the bulk action.
    expect(screen.getByText('Needs review')).toBeInTheDocument()

    // Select the row via its row checkbox (first checkbox = header select-all
    // when present, which selects this row too).
    const checkboxes = await screen.findAllByRole('checkbox')
    await user.click(checkboxes[0])
    const markButton = await screen.findByTitle(t('transactions.markReviewed'))
    expect(markButton).toBeInTheDocument()

    await user.click(markButton)

    await waitFor(() =>
      expect(api.transactions.bulkReview).toHaveBeenCalledWith(['tx-1'], 'reviewed'),
    )
    // Selection cleared: the bulk bar (and its buttons) unmount.
    await waitFor(() =>
      expect(screen.queryByTitle(t('transactions.markReviewed'))).not.toBeInTheDocument(),
    )
    // Rows updated after the invalidation refetch: inbox empty.
    await waitFor(() =>
      expect(screen.queryByText('Needs review')).not.toBeInTheDocument(),
    )
  })

  it('test: row indicator reflects review_status', async () => {
    api.transactions.list.mockResolvedValue(
      envelope(
        [
          makeTx({ id: 'tx-1', description: 'Needs one', review_status: 'needs_review' }),
          makeTx({ id: 'tx-2', description: 'Done one', review_status: 'reviewed' }),
        ],
        1,
      ),
    )
    renderPage()
    await screen.findByText('Needs one')

    // Exactly one needs-review pill: on the needs-review row only.
    expect(screen.getAllByText('Needs review')).toHaveLength(1)
    expect(
      screen.getAllByRole('img', { name: 'Needs review' }).length,
    ).toBeGreaterThanOrEqual(1)
  })

  it('test: menu row toggles needs-review filter', async () => {
    const { user } = renderPage()
    await screen.findByText('Needs one')

    await user.click(
      screen.getByRole('button', { name: t('transactions.filtersBar.filters') }),
    )

    // Menu content portals to document.body (Radix), so query there. The
    // switch row lives in the menu's desktop section — the mobile root
    // list carries the same label, hence the scoping.
    const menuContent = document.querySelector('[role="menu"]')
    expect(menuContent).not.toBeNull()
    const desktopSection = (menuContent as HTMLElement).querySelector('div.hidden')
    expect(desktopSection).not.toBeNull()
    const menuRow = within(desktopSection as HTMLElement).getByText(
      t('transactions.needsReviewOnly'),
    )

    await user.click(menuRow)
    await waitFor(() =>
      expect(api.transactions.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ review_status: 'needs_review' }),
      ),
    )

    // Menu stays open (switch rows preventDefault), so the same row toggles back.
    const desktopSection2 = (document.querySelector('[role="menu"]') as HTMLElement).querySelector('div.hidden')
    const menuRowAgain = within(desktopSection2 as HTMLElement).getByText(
      t('transactions.needsReviewOnly'),
    )
    await user.click(menuRowAgain)
    await waitFor(() =>
      expect(api.transactions.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ review_status: undefined }),
      ),
    )
  })

  it.each([false, true])('qualification mobile flag action and state: flagged=%s', async (flagged) => {
    platform.isMobile = true
    const row = makeTx({ flagged })
    api.transactions.list.mockResolvedValue(envelope([row], 1))
    api.transactions.update.mockResolvedValue({ ...row, flagged: !flagged })
    const { user } = renderPage()
    await screen.findByText('Needs one')
    const action = screen.getByRole('button', { name: t(flagged ? 'transactions.unflag' : 'transactions.flag') })
    await user.click(action)
    await waitFor(() => expect(api.transactions.update).toHaveBeenCalledWith('tx-1', { flagged: !flagged }))
    expect(api.transactions.setReview).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it.each(['viewer', 'shared'])('qualification mobile read-only flag state: %s', async (kind) => {
    platform.isMobile = true
    platform.canWrite = kind !== 'viewer'
    api.transactions.list.mockResolvedValue(envelope([makeTx({ flagged: true, is_shared: kind === 'shared' })], 1))
    renderPage()
    await screen.findByText('Needs one')
    expect(screen.queryByRole('button', { name: t('transactions.flag') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('transactions.unflag') })).not.toBeInTheDocument()
    expect(api.transactions.update).not.toHaveBeenCalled()
    expect(screen.getByLabelText(t('transactions.flagged'))).toBeInTheDocument()
  })

  it.each(['pending', 'error'])('mobile flag mutation exposes %s without changing review', async (mode) => {
    platform.isMobile = true
    let finish!: (value: unknown) => void
    const row = makeTx({ flagged: false })
    api.transactions.list.mockResolvedValue(envelope([row], 1))
    if (mode === 'pending') api.transactions.update.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    else api.transactions.update.mockRejectedValue({ response: { data: { detail: 'Synthetic flag save rejected' } } })
    const { user } = renderPage()
    await screen.findByText('Needs one')
    const button = screen.getByRole('button', { name: t('transactions.flag') })
    await user.click(button)
    await waitFor(() => expect(api.transactions.update).toHaveBeenCalledTimes(1))
    if (mode === 'pending') {
      expect(button).toBeDisabled()
      await user.click(button)
      expect(api.transactions.update).toHaveBeenCalledTimes(1)
      finish({ ...row, flagged: true })
      await waitFor(() => expect(button).toBeEnabled())
    } else {
      await waitFor(() => expect(button).toBeEnabled())
      expect(feedback.error).toHaveBeenCalledWith('Synthetic flag save rejected')
      expect(screen.queryByRole('img', { name: t('transactions.flagged') })).not.toBeInTheDocument()
    }
    expect(api.transactions.setReview).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('qualification desktop viewer cannot invoke flag action', async () => {
    platform.canWrite = false
    renderPage()
    await screen.findByText('Needs one')
    expect(screen.queryByRole('button', { name: t('transactions.flag') })).not.toBeInTheDocument()
  })
})
