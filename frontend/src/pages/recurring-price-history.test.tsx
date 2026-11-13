/**
 * Read-only price-history surface on the Recurring page.
 *
 * Only the recurring GET methods are mocked; copy is asserted through the
 * real English/pt-BR bundles so a deleted or drifted key fails here, not in
 * production. Decimal amounts cross the wire as JSON strings; the fixtures
 * keep that shape (see `increaseEvent`) and the UI must coerce before formatting.
 */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import RecurringPage from '@/pages/recurring'
import { formatCurrency } from '@/lib/format'
import { i18n, renderWithProviders, t } from '@/test/utils'
import type { RecurringPriceChange, RecurringPriceChangeHistory, RecurringTransaction } from '@/types'
import gapWitness from '@/test/fixtures/recurring-price-change-31-days.json'

const api = vi.hoisted(() => ({
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  currencies: { list: vi.fn() },
  recurring: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    generate: vi.fn(),
    recentPriceChanges: vi.fn(),
    priceHistory: vi.fn(),
  },
}))

const workspace = vi.hoisted(() => ({ canWrite: true }))
const privacy = vi.hoisted(() => ({ enabled: false }))
const display = vi.hoisted(() => ({ locale: 'en-US' }))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => display.locale,
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))

vi.mock('@/contexts/workspace-context', async (importOriginal) => {
  // CategorySelect reads the WorkspaceContext object itself, so keep the real
  // module and override only the hook the page consumes.
  const actual = await importOriginal<typeof import('@/contexts/workspace-context')>()
  return {
    ...actual,
    useWorkspace: () => ({ canWrite: workspace.canWrite }),
  }
})

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({
    // Mirrors the real hook: privacy mode masks the whole formatted string.
    mask: (value: string) => (privacy.enabled ? '•••••' : value),
  }),
}))

const netflix: RecurringTransaction = {
  id: 'rt-netflix',
  user_id: 'u1',
  account_id: null,
  category_id: null,
  description: 'Netflix',
  amount: 39.9,
  currency: 'BRL',
  type: 'debit',
  frequency: 'monthly',
  weekend_adjustment: 'none',
  day_of_month: null,
  start_date: '2026-01-01',
  end_date: null,
  is_active: true,
  auto_generate: true,
  next_occurrence: '2026-04-01',
  amount_primary: null,
  fx_rate_used: null,
}

const gym: RecurringTransaction = {
  ...netflix,
  id: 'rt-gym',
  description: 'Gym',
  amount: 21.9,
}

// The backend emits Decimals as two-place JSON strings; the UI coerces.
const increaseEvent: RecurringPriceChange = {
  recurring_id: 'rt-netflix',
  previous_transaction_id: 'tx-1',
  current_transaction_id: 'tx-2',
  previous_date: '2026-01-01',
  current_date: '2026-02-01',
  previous_amount: '39.90',
  current_amount: '45.90',
  currency: 'BRL',
  direction: 'increase',
  absolute_delta: '6.00',
  percent_delta: '15.04',
  gap_days: 31,
  baseline_occurrence_count: 2,
}

const decreaseEvent: RecurringPriceChange = {
  recurring_id: 'rt-gym',
  previous_transaction_id: 'tx-3',
  current_transaction_id: 'tx-4',
  previous_date: '2026-01-05',
  current_date: '2026-02-05',
  previous_amount: '21.90',
  current_amount: '19.90',
  currency: 'BRL',
  direction: 'decrease',
  absolute_delta: '2.00',
  percent_delta: '-9.13',
  gap_days: 31,
  baseline_occurrence_count: 2,
}

const insufficientHistory: RecurringPriceChangeHistory = {
  recurring_id: 'rt-netflix',
  eligible_occurrence_count: 2,
  eligibility: 'insufficient_history',
  changes: [],
}

const money = (value: number) => formatCurrency(value, 'BRL', 'en-US')

function seriesRow(description: string) {
  const rows = screen.getAllByRole('row')
  const row = rows.find((element) => within(element).queryByText(description))
  if (!row) throw new Error(`No table row for ${description}`)
  return row
}

function historyButton(description: string) {
  return within(seriesRow(description)).getByRole('button', {
    name: t('recurring.viewPriceHistory'),
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  privacy.enabled = false
  display.locale = 'en-US'
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.recurring.list.mockResolvedValue([netflix, gym])
  api.recurring.recentPriceChanges.mockResolvedValue([])
  api.recurring.priceHistory.mockResolvedValue(insufficientHistory)
  api.recurring.generate.mockResolvedValue({ generated: 0 })
  api.recurring.create.mockResolvedValue(netflix)
  api.recurring.update.mockResolvedValue(netflix)
  api.recurring.delete.mockResolvedValue(undefined)
})

afterEach(async () => {
  await i18n.changeLanguage('en')
})

describe('Recurring price history', () => {
  it.each([
    ['en-US', '15.04', '+15.04%'],
    ['en-US', '-9.13', '-9.13%'],
    ['en-US', '0.00', '0.00%'],
    ['de-DE', '15.04', '+15,04 %'],
    ['de-DE', '-9.13', '-9,13 %'],
    ['de-DE', '0.00', '0,00 %'],
  ])('formats percentage units and precision for %s / %s', async (locale, percent, expected) => {
    display.locale = locale
    api.recurring.recentPriceChanges.mockResolvedValue([
      { ...increaseEvent, percent_delta: percent },
    ])
    renderWithProviders(<RecurringPage />, { route: '/recurring' })
    const row = await screen.findByTestId('price-change-row')
    // Normalize only Intl's nonbreaking whitespace, not decimal/sign/units.
    expect(row.textContent?.replace(/\s/g, ' ')).toContain(`(${expected})`)
    expect(within(row).getByText(
      `${formatCurrency(39.9, 'BRL', locale)} → ${formatCurrency(45.9, 'BRL', locale)}`,
      { normalizer: value => value }
    )).toBeInTheDocument()
    expect(within(seriesRow('Netflix')).getByText('4/1/2026')).toBeInTheDocument()
  })

  it('shows an error instead of unnamed recent events when the series list fails', async () => {
    api.recurring.list.mockRejectedValue(new Error('list failed'))
    api.recurring.recentPriceChanges.mockResolvedValue([increaseEvent])
    renderWithProviders(<RecurringPage />, { route: '/recurring' })
    expect(await screen.findByText(t('common.error'))).toBeInTheDocument()
    expect(screen.queryByTestId('price-change-row')).not.toBeInTheDocument()
    expect(api.recurring.recentPriceChanges).toHaveBeenCalledWith(20)
    expect(api.recurring.priceHistory).not.toHaveBeenCalled()
  })

  it('keeps cached series names usable when a list refetch fails', async () => {
    api.recurring.recentPriceChanges.mockResolvedValue([increaseEvent])
    const { queryClient } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    const row = await screen.findByTestId('price-change-row')
    expect(within(row).getByText('Netflix')).toBeInTheDocument()
    api.recurring.list.mockRejectedValue(new Error('refetch failed'))
    await queryClient.refetchQueries({ queryKey: ['recurring'], exact: true })
    await waitFor(() => expect(queryClient.getQueryState(['recurring'])?.status).toBe('error'))
    expect(within(row).getByText('Netflix')).toBeInTheDocument()
    expect(screen.queryByText(t('common.error'))).not.toBeInTheDocument()
  })

  it('preserves recent loading and missing-series response behavior', async () => {
    api.recurring.list.mockResolvedValue([])
    api.recurring.recentPriceChanges.mockReturnValue(new Promise(() => {}))
    const { queryClient } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    expect(await screen.findByText(t('common.loading'))).toBeInTheDocument()
    queryClient.setQueryData(['recurring', 'recent-price-changes', 20], [increaseEvent])
    expect(await screen.findByTestId('price-change-row')).toBeInTheDocument()
    expect(screen.queryByText(t('common.error'))).not.toBeInTheDocument()
  })

  it.each(['recent', 'history'])('preserves the %s request error state', async (request) => {
    const { user, queryClient } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')
    if (request === 'recent') {
      api.recurring.recentPriceChanges.mockRejectedValue(new Error('recent failed'))
      await queryClient.refetchQueries({ queryKey: ['recurring', 'recent-price-changes'] })
      expect(await screen.findByText(t('common.error'))).toBeInTheDocument()
    } else {
      api.recurring.priceHistory.mockRejectedValue(new Error('history failed'))
      await user.click(historyButton('Netflix'))
      const dialog = await screen.findByRole('dialog')
      expect(await within(dialog).findByText(t('common.error'))).toBeInTheDocument()
    }
  })

  it('keeps zero and pending history content in the scrollable body', async () => {
    api.recurring.list.mockResolvedValue([{ ...netflix, amount: 0 }])
    api.recurring.priceHistory.mockReturnValue(new Promise(() => {}))
    const { user, queryClient } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')
    await user.click(historyButton('Netflix'))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(`−${money(0)}`)
    expect(within(dialog).getByText(t('common.loading'))).toBeInTheDocument()
    queryClient.setQueryData(['recurring', 'price-history', netflix.id], {
      recurring_id: netflix.id,
      eligible_occurrence_count: 0,
      eligibility: 'insufficient_history',
      changes: [],
    } satisfies RecurringPriceChangeHistory)
    expect(await within(dialog).findByText(t('recurring.priceChangeInsufficient'))).toBeInTheDocument()
    expect(within(dialog).getByRole('heading')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps a long history body scrollable outside its header and close button', async () => {
    api.recurring.priceHistory.mockResolvedValue({
      recurring_id: netflix.id,
      eligible_occurrence_count: 202,
      eligibility: 'changes_detected',
      changes: Array.from({ length: 200 }, (_, index) => ({
        ...increaseEvent, current_transaction_id: `history-${index}`,
      })),
    } satisfies RecurringPriceChangeHistory)
    const { user } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')
    await user.click(historyButton('Netflix'))
    const dialog = await screen.findByRole('dialog')
    const rows = await within(dialog).findAllByTestId('price-change-row')
    expect(rows).toHaveLength(200)
    expect(dialog).toHaveClass('max-h-[90vh]', 'flex', 'flex-col', 'overflow-hidden')
    const body = rows[0].closest('.overflow-y-auto')
    expect(body).toHaveClass('min-h-0')
    expect(body).not.toContainElement(within(dialog).getByRole('heading'))
    const close = within(dialog).getByRole('button', { name: 'Close' })
    expect(body).not.toContainElement(close)
    await user.click(close)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders recent price changes with increase and decrease', async () => {
    api.recurring.recentPriceChanges.mockResolvedValue([increaseEvent, decreaseEvent])

    renderWithProviders(<RecurringPage />, { route: '/recurring' })

    // old -> new, absolute delta and signed percent, localized direction.
    expect(await screen.findByText(`${money(39.9)} → ${money(45.9)}`)).toBeInTheDocument()
    expect(
      screen.getByText(t('recurring.priceChangeDelta', { absolute: money(6), percent: '+15.04%' }))
    ).toBeInTheDocument()
    expect(screen.getByText('Price increased')).toBeInTheDocument()

    expect(screen.getByText(`${money(21.9)} → ${money(19.9)}`)).toBeInTheDocument()
    expect(
      screen.getByText(t('recurring.priceChangeDelta', { absolute: money(2), percent: '-9.13%' }))
    ).toBeInTheDocument()
    expect(screen.getByText('Price decreased')).toBeInTheDocument()

    // Series descriptions join the events to their rows.
    expect(screen.getAllByTestId('price-change-row')).toHaveLength(2)
    expect(screen.getByRole('heading', { name: t('recurring.priceChangesTitle') })).toBeInTheDocument()
  })

  it('opens price history for the selected series', async () => {
    api.recurring.priceHistory.mockResolvedValue({
      recurring_id: 'rt-gym',
      eligible_occurrence_count: 3,
      eligibility: 'changes_detected',
      changes: [decreaseEvent],
    } satisfies RecurringPriceChangeHistory)

    const { user } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')

    await user.click(historyButton('Gym'))

    expect(api.recurring.priceHistory).toHaveBeenCalledWith('rt-gym')
    // Only the selected series' id is requested for detail.
    expect(api.recurring.priceHistory).toHaveBeenCalledTimes(1)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(t('recurring.priceHistory'))).toBeInTheDocument()
    expect(within(dialog).getByText(t('recurring.priceChangeBadge'))).toBeInTheDocument()
    expect(within(dialog).getByText(`${money(21.9)} → ${money(19.9)}`)).toBeInTheDocument()
    expect(within(dialog).getByText('Price decreased')).toBeInTheDocument()
  })

  it('hides Recent when no stable change is returned', async () => {
    renderWithProviders(<RecurringPage />, { route: '/recurring' })

    await screen.findByText('Netflix')
    await waitFor(() => expect(screen.queryByText(t('common.loading'))).not.toBeInTheDocument())
    expect(screen.queryByText(t('recurring.priceChangesEmpty'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('recurring.priceChangeBadge'))).not.toBeInTheDocument()
  })

  it('shows insufficient history explanation', async () => {
    const { user } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')

    await user.click(historyButton('Netflix'))

    expect(await screen.findByText(t('recurring.priceChangeInsufficient'))).toBeInTheDocument()
    expect(screen.queryByText(t('recurring.priceChangeBadge'))).not.toBeInTheDocument()
  })

  it('shows no qualifying change history without claiming insufficient observations', async () => {
    api.recurring.priceHistory.mockResolvedValue({
      recurring_id: netflix.id,
      eligible_occurrence_count: 4,
      eligibility: 'no_qualifying_change',
      changes: [],
    } satisfies RecurringPriceChangeHistory)
    const { user } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')
    await user.click(historyButton('Netflix'))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(t('recurring.priceChangesEmpty'))).toBeInTheDocument()
    expect(within(dialog).queryByText(t('recurring.priceChangeInsufficient'))).not.toBeInTheDocument()
  })

  it('shows gap note', async () => {
    api.recurring.recentPriceChanges.mockResolvedValue([
      { ...increaseEvent, previous_date: '2026-10-01', current_date: '2026-12-01', gap_days: 61 },
    ])

    renderWithProviders(<RecurringPage />, { route: '/recurring' })

    expect(await screen.findByText(t('recurring.priceChangeGap', { days: 61 }))).toBeInTheDocument()
  })

  it('does not show a one-cent rounding event', async () => {
    // The backend filtered the one-cent move out; the response carries no event.
    api.recurring.recentPriceChanges.mockResolvedValue([])

    renderWithProviders(<RecurringPage />, { route: '/recurring' })

    await screen.findByText('Netflix')
    await waitFor(() => expect(screen.queryByText(t('common.loading'))).not.toBeInTheDocument())
    expect(screen.queryByText(t('recurring.priceChangesEmpty'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('recurring.priceChangeBadge'))).not.toBeInTheDocument()
    expect(screen.queryByText('Price increased')).not.toBeInTheDocument()
    expect(screen.queryByTestId('price-change-row')).not.toBeInTheDocument()
  })

  it('keeps current recurring CRUD actions working', async () => {
    const { user } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')

    // Generate, edit and delete stay reachable alongside the new action.
    const generateButton = screen.getByRole('button', { name: t('recurring.generatePending') })
    await user.click(generateButton)
    expect(api.recurring.generate).toHaveBeenCalledTimes(1)

    // Assert the row controls before opening a modal dialog: Radix marks the
    // page behind a modal as inert, so background rows leave the a11y tree.
    expect(
      within(seriesRow('Netflix')).getByRole('button', { name: t('common.delete') })
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('recurring.add') })).toBeInTheDocument()
    expect(
      screen.getAllByRole('button', { name: t('recurring.viewPriceHistory') })
    ).toHaveLength(2)

    await user.click(within(seriesRow('Netflix')).getByRole('button', { name: t('common.edit') }))
    expect(await screen.findByText(t('recurring.edit'))).toBeInTheDocument()
  })

  it.each([increaseEvent, decreaseEvent])('masks $direction amounts and percent in the list', async (event) => {
    privacy.enabled = true
    api.recurring.recentPriceChanges.mockResolvedValue([event])

    renderWithProviders(<RecurringPage />, { route: '/recurring' })

    // The table amount cells and the price-change row mask exactly alike.
    expect(await screen.findByText('••••• → •••••')).toBeInTheDocument()
    expect(screen.getAllByText('•••••').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText(money(39.9))).not.toBeInTheDocument()
    expect(screen.queryByText(`${money(39.9)} → ${money(45.9)}`)).not.toBeInTheDocument()
    for (const value of [event.previous_amount, event.current_amount, event.absolute_delta, event.percent_delta]) {
      // Covers visible/hidden text and aria/title/tooltip attributes alike.
      expect(document.body.innerHTML).not.toContain(value)
    }
    expect(screen.getByText(t('recurring.priceChangeDelta', { absolute: '•••••', percent: '•••••' }))).toBeVisible()
  })

  it.each([increaseEvent, decreaseEvent])('masks $direction amounts and percent in the dialog', async (event) => {
    privacy.enabled = true
    api.recurring.priceHistory.mockResolvedValue({
      recurring_id: event.recurring_id,
      eligible_occurrence_count: 3,
      eligibility: 'changes_detected',
      changes: [event],
    } satisfies RecurringPriceChangeHistory)
    const { user } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')
    await user.click(historyButton(event.direction === 'increase' ? 'Netflix' : 'Gym'))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('••••• → •••••')).toBeVisible()
    for (const value of [event.previous_amount, event.current_amount, event.absolute_delta, event.percent_delta]) {
      expect(document.body.innerHTML).not.toContain(value)
    }
    expect(within(dialog).getByText(t('recurring.priceChangeDelta', { absolute: '•••••', percent: '•••••' }))).toBeVisible()
  })

  it('provides accessible history action label', async () => {
    workspace.canWrite = false // read-only workspace: the action must still exist

    renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')

    const buttons = screen.getAllByRole('button', { name: t('recurring.viewPriceHistory') })
    expect(buttons).toHaveLength(2)
    // Read-only workspaces get no mutation controls.
    expect(screen.queryByRole('button', { name: t('recurring.add') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('common.edit') })).not.toBeInTheDocument()
  })

  it('pt-BR labels resolve', async () => {
    await i18n.changeLanguage('pt-BR')
    api.recurring.recentPriceChanges.mockResolvedValue([increaseEvent])

    renderWithProviders(<RecurringPage />, { route: '/recurring' })

    // Wait for query content before asserting the populated card heading.
    expect(await screen.findByText('Preço aumentou')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: t('recurring.priceChangesTitle') })).toBeInTheDocument()
    const buttons = await screen.findAllByRole('button', { name: 'Ver histórico de preços' })
    expect(buttons).toHaveLength(2)
    // No raw key ever reaches the screen.
    expect(screen.queryByText(/recurring\./)).not.toBeInTheDocument()
  })
})

describe('Recurring maintainer correction regressions', () => {
  it('hides the entire Recent card after a successful empty response', async () => {
    const { container, queryClient } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')
    await waitFor(() => expect(queryClient.getQueryState(
      ['recurring', 'recent-price-changes', 20]
    )?.status).toBe('success'))
    expect(screen.queryByRole('heading', { name: t('recurring.priceChangesTitle') })).not.toBeInTheDocument()
    expect(screen.queryByText(t('recurring.priceChangesEmpty'))).not.toBeInTheDocument()
    // The recurring table remains; no empty card shell is left behind.
    expect(container.querySelectorAll('.bg-card')).toHaveLength(1)
    expect(historyButton('Netflix')).toBeInTheDocument()
  })

  it('removes a populated Recent card when refetch succeeds empty', async () => {
    api.recurring.recentPriceChanges.mockResolvedValue([increaseEvent])
    const { queryClient } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByTestId('price-change-row')
    const heading = screen.getByRole('heading', { name: t('recurring.priceChangesTitle') })
    const card = heading.closest('.bg-card')
    expect(card).not.toBeNull()
    api.recurring.recentPriceChanges.mockResolvedValue([])
    await queryClient.refetchQueries({ queryKey: ['recurring', 'recent-price-changes'] })
    await waitFor(() => expect(screen.queryByTestId('price-change-row')).not.toBeInTheDocument())
    expect(heading).not.toBeInTheDocument()
    expect(card).not.toBeInTheDocument()
    expect(screen.queryByText(t('recurring.priceChangesEmpty'))).not.toBeInTheDocument()
  })

  const effects = [
    ['debit', 'increase', increaseEvent, 'text-rose-500'],
    ['debit', 'decrease', decreaseEvent, 'text-emerald-600'],
    ['credit', 'increase', increaseEvent, 'text-emerald-600'],
    ['credit', 'decrease', decreaseEvent, 'text-rose-500'],
  ] as const

  for (const surface of ['Recent', 'history'] as const) {
    it.each(effects)(`${surface} colors %s %s by economic effect`, async (type, direction, source, color) => {
      expect(source.direction).toBe(direction)
      const event = { ...source, recurring_id: netflix.id }
      api.recurring.list.mockResolvedValue([{ ...netflix, type, amount: Number(event.current_amount) }])
      api.recurring.recentPriceChanges.mockResolvedValue(surface === 'Recent' ? [event] : [])
      api.recurring.priceHistory.mockResolvedValue({
        recurring_id: netflix.id, eligible_occurrence_count: 3,
        eligibility: 'changes_detected', changes: [event],
      } satisfies RecurringPriceChangeHistory)
      const { user } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
      await within(await screen.findByRole('table')).findByText('Netflix')
      if (surface === 'history') await user.click(historyButton('Netflix'))
      const scope = surface === 'history' ? within(await screen.findByRole('dialog')) : screen
      const row = await scope.findByTestId('price-change-row')
      const directionKey = event.direction === 'increase' ? 'priceChangeIncrease' : 'priceChangeDecrease'
      const indicator = within(row).getByText(t(`recurring.${directionKey}`))
      expect(indicator).toHaveClass(color)
      expect(indicator).not.toHaveClass(color === 'text-rose-500' ? 'text-emerald-600' : 'text-rose-500')
      expect(indicator.querySelector('svg')).toHaveClass(
        event.direction === 'increase' ? 'lucide-trending-up' : 'lucide-trending-down'
      )
      expect(row).toHaveTextContent(`${money(Number(event.previous_amount))} → ${money(Number(event.current_amount))}`)
      expect(api.recurring.create).not.toHaveBeenCalled()
      expect(api.recurring.update).not.toHaveBeenCalled()
      expect(api.recurring.delete).not.toHaveBeenCalled()
      expect(api.recurring.generate).not.toHaveBeenCalled()
    })

    it(`${surface} renders the API-verified adjacent-observation 31-day witness`, async () => {
      const event = gapWitness as RecurringPriceChange
      expect((Date.parse(event.current_date) - Date.parse(event.previous_date)) / 86_400_000).toBe(31)
      api.recurring.list.mockResolvedValue([{ ...netflix, id: event.recurring_id }])
      api.recurring.recentPriceChanges.mockResolvedValue(surface === 'Recent' ? [event] : [])
      api.recurring.priceHistory.mockResolvedValue({
        recurring_id: event.recurring_id, eligible_occurrence_count: 3,
        eligibility: 'changes_detected', changes: [event],
      } satisfies RecurringPriceChangeHistory)
      const { user } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
      await within(await screen.findByRole('table')).findByText('Netflix')
      if (surface === 'history') await user.click(historyButton('Netflix'))
      const scope = surface === 'history' ? within(await screen.findByRole('dialog')) : screen
      const row = await scope.findByTestId('price-change-row')
      expect(within(row).getByText('After a 31-day gap')).toBeVisible()
      expect(within(row).queryByText(/21-day/)).not.toBeInTheDocument()
      expect(row).toHaveTextContent(`${money(10)} → ${money(12)}`)
      expect(row).toHaveTextContent(`${money(2)} (+20.00%)`)
    })
  }

  it('uses neutral color when Recent cannot resolve the series type', async () => {
    api.recurring.list.mockResolvedValue([])
    api.recurring.recentPriceChanges.mockResolvedValue([increaseEvent])
    renderWithProviders(<RecurringPage />, { route: '/recurring' })
    const row = await screen.findByTestId('price-change-row')
    const indicator = within(row).getByText(t('recurring.priceChangeIncrease'))
    expect(indicator).toHaveClass('text-muted-foreground')
    expect(indicator).not.toHaveClass('text-rose-500', 'text-emerald-600')
  })

  it('keeps the generic history badge economically neutral', async () => {
    api.recurring.priceHistory.mockResolvedValue({
      recurring_id: netflix.id, eligible_occurrence_count: 3,
      eligibility: 'changes_detected', changes: [increaseEvent],
    } satisfies RecurringPriceChangeHistory)
    const { user } = renderWithProviders(<RecurringPage />, { route: '/recurring' })
    await screen.findByText('Netflix')
    await user.click(historyButton('Netflix'))
    const dialog = await screen.findByRole('dialog')
    const badge = await within(dialog).findByText(t('recurring.priceChangeBadge'))
    expect(badge).toHaveClass('text-muted-foreground')
    expect(badge).not.toHaveClass('text-rose-500', 'text-emerald-600')
  })
})
