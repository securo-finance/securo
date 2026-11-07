import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import InvoiceSchedulesPage from '@/pages/invoice-schedules'
import { renderWithProviders, t } from '@/test/utils'
import type { InvoiceSchedule, InvoiceScheduleSummary } from '@/types'

const api = vi.hoisted(() => ({
  invoiceSchedules: {
    list: vi.fn(),
    summary: vi.fn(),
    create: vi.fn(),
  },
  invoices: { settings: vi.fn() },
  payees: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  invoiceSchedules: api.invoiceSchedules,
  invoices: api.invoices,
  payees: api.payees,
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

let canWrite = true
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

const term = {
  id: 'term-1',
  effective_from: '2026-01-05',
  lines: [{ description: 'Retainer', quantity: '1', unit_price: '3000.00' }],
  discount: '0.00',
  subtotal: '3000.00',
  tax_total: '0.00',
  total: '3000.00',
  created_at: '2026-01-01T00:00:00Z',
}

function schedule(overrides: Partial<InvoiceSchedule> = {}): InvoiceSchedule {
  return {
    id: 'sched-1',
    name: 'Plano Pro',
    payee_id: 'alpha',
    payee: { id: 'alpha', name: 'Cliente Alpha' },
    origin: 'local',
    external_source: null,
    external_id: null,
    status: 'active',
    pause_reason: null,
    ended_at: null,
    end_reason: null,
    frequency: 'monthly',
    start_date: '2026-01-05',
    end_type: 'never',
    end_date: null,
    end_count: null,
    payment_terms_days: null,
    currency: 'USD',
    notes: null,
    custom_fields: null,
    next_sequence: 10,
    last_generated_at: null,
    consecutive_failures: 0,
    terms: [term],
    created_at: '2026-01-01T00:00:00Z',
    current_term: term,
    next_term: term,
    monthly_amount: '3000.00',
    next_period_start: '2026-10-05',
    invoice_count: 9,
    amount_invoiced: '27000.00',
    amount_paid: '24000.00',
    past_due_count: 0,
    ...overrides,
  }
}

const summary: InvoiceScheduleSummary = {
  active_count: 2,
  paused_count: 1,
  ended_count: 1,
  by_currency: [
    {
      currency: 'USD',
      monthly_recurring: '3300.00',
      active_count: 2,
      ended_recently_count: 1,
      monthly_lost: '500.00',
      past_due_count: 1,
    },
  ],
}

describe('InvoiceSchedulesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    canWrite = true
    api.invoiceSchedules.summary.mockResolvedValue(summary)
    api.invoiceSchedules.list.mockResolvedValue([
      schedule(),
      schedule({
        id: 'sched-2',
        name: 'Hosting',
        frequency: 'quarterly',
        monthly_amount: '300.00',
        past_due_count: 1,
        current_term: { ...term, id: 'term-2', total: '900.00' },
      }),
      schedule({
        id: 'sched-3',
        name: 'Old retainer',
        status: 'ended',
        ended_at: '2026-09-01',
        end_reason: 'canceled_by_client',
        monthly_amount: '0.00',
        next_period_start: null,
      }),
    ])
    api.invoices.settings.mockResolvedValue({ default_payment_terms_days: 30, tax_fields: 'hidden' })
    api.payees.list.mockResolvedValue([])
  })

  it('reads the financial summary the server derived', async () => {
    renderWithProviders(<InvoiceSchedulesPage />, { route: '/invoices/schedules' })
    const card = await screen.findByTestId('schedules-summary-USD')
    expect(within(card).getByText('$3,300.00')).toBeInTheDocument()
    expect(within(card).getByText('$39,600.00')).toBeInTheDocument()
    expect(within(card).getByText('$500.00')).toBeInTheDocument()
    expect(within(card).getByText(t('invoices.schedules.summary.endedRecently', { count: 1 }))).toBeInTheDocument()
    expect(within(card).getByText(t('invoices.schedules.summary.activeCount', { count: 2 }))).toBeInTheDocument()
  })

  it('lists every agreement with its cadence, monthly worth and state', async () => {
    renderWithProviders(<InvoiceSchedulesPage />, { route: '/invoices/schedules' })
    const rows = await screen.findAllByTestId('schedule-row')
    expect(rows).toHaveLength(3)

    const pro = rows[0]
    expect(within(pro).getByText('Plano Pro')).toBeInTheDocument()
    expect(within(pro).getByText('Cliente Alpha')).toBeInTheDocument()
    expect(within(pro).getByText('$3,000.00')).toBeInTheDocument()
    expect(within(pro).getByTestId('schedule-state-active')).toBeInTheDocument()

    // A late invoice outranks "active" on the pill: it is the thing to act on.
    const hosting = rows[1]
    expect(within(hosting).getByText(t('invoices.schedules.frequency.quarterly'), { exact: false })).toBeInTheDocument()
    expect(within(hosting).getByTestId('schedule-state-past_due')).toBeInTheDocument()

    // Ended agreements are worth nothing a month and have no next invoice.
    const old = rows[2]
    expect(within(old).getByTestId('schedule-state-ended')).toBeInTheDocument()
    expect(within(old).queryByText('$3,000.00')).not.toBeInTheDocument()
  })

  it('filters by status through the server, with counts from the summary', async () => {
    const { user } = renderWithProviders(<InvoiceSchedulesPage />, { route: '/invoices/schedules' })
    await screen.findAllByTestId('schedule-row')
    expect(screen.getByTestId('schedule-filter-all-count')).toHaveTextContent('4')
    expect(screen.getByTestId('schedule-filter-paused-count')).toHaveTextContent('1')

    await user.click(screen.getByTestId('schedule-filter-ended'))
    expect(api.invoiceSchedules.list).toHaveBeenLastCalledWith({ status: 'ended' })
  })

  it('offers to create only to a member who can write', async () => {
    canWrite = false
    renderWithProviders(<InvoiceSchedulesPage />, { route: '/invoices/schedules' })
    await screen.findAllByTestId('schedule-row')
    expect(screen.queryByTestId('schedule-new-button')).not.toBeInTheDocument()
  })

  it('previews the monthly worth while the agreement is being written', async () => {
    const { user } = renderWithProviders(<InvoiceSchedulesPage />, { route: '/invoices/schedules' })
    await screen.findAllByTestId('schedule-row')
    await user.click(screen.getByTestId('schedule-new-button'))
    await screen.findByTestId('schedule-name-input')

    // Submit stays off until there is a name and something to bill.
    expect(screen.getByTestId('schedule-create-submit')).toBeDisabled()
    await user.type(screen.getByTestId('schedule-name-input'), 'Retainer')
    await user.type(screen.getByTestId('invoice-line-description-0'), 'Monthly retainer')
    await user.clear(screen.getByTestId('invoice-line-price-0'))
    await user.type(screen.getByTestId('invoice-line-price-0'), '1200')

    expect(screen.getByTestId('schedule-monthly-preview')).toHaveTextContent('$1,200.00')
    expect(screen.getByTestId('schedule-create-submit')).toBeEnabled()
  })
})
