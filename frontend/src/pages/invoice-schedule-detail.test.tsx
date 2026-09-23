import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import InvoiceScheduleDetailPage from '@/pages/invoice-schedule-detail'
import { renderWithProviders, t } from '@/test/utils'
import type { Invoice, InvoiceSchedule, InvoiceScheduleTerm } from '@/types'

const api = vi.hoisted(() => ({
  invoiceSchedules: {
    get: vi.fn(),
    invoices: vi.fn(),
    periods: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    end: vi.fn(),
    generate: vi.fn(),
    remove: vi.fn(),
    addTerm: vi.fn(),
    updateTerm: vi.fn(),
    removeTerm: vi.fn(),
    link: vi.fn(),
    update: vi.fn(),
  },
  invoices: { settings: vi.fn(), list: vi.fn() },
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

let canWrite = true
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

const FAR_FUTURE = new Date(Date.now() + 200 * 86_400_000).toISOString().slice(0, 10)

function term(overrides: Partial<InvoiceScheduleTerm> = {}): InvoiceScheduleTerm {
  return {
    id: 'term-1',
    effective_from: '2026-01-05',
    lines: [{ description: 'Retainer', quantity: '1', unit_price: '3000.00' }],
    discount: '0.00',
    subtotal: '3000.00',
    tax_total: '0.00',
    total: '3000.00',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const current = term()
const raise_ = term({ id: 'term-2', effective_from: FAR_FUTURE, total: '3500.00', subtotal: '3500.00' })

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
    payment_terms_days: 10,
    currency: 'USD',
    notes: null,
    custom_fields: null,
    next_sequence: 3,
    last_generated_at: null,
    consecutive_failures: 0,
    terms: [current, raise_],
    created_at: '2026-01-01T00:00:00Z',
    current_term: current,
    next_term: current,
    monthly_amount: '3000.00',
    next_period_start: '2026-03-05',
    invoice_count: 2,
    amount_invoiced: '6000.00',
    amount_paid: '3000.00',
    past_due_count: 1,
    ...overrides,
  }
}

function invoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: 'inv-1',
    payee_id: 'alpha',
    payee: { id: 'alpha', name: 'Cliente Alpha' },
    document_type: 'invoice',
    direction: 'receivable',
    origin: 'local',
    external_source: null,
    external_id: null,
    number: 12,
    series: null,
    external_number: null,
    status: 'open',
    state: 'paid',
    issue_date: '2026-01-05',
    due_date: '2026-01-15',
    competence_date: '2026-01-05',
    sent_at: null,
    currency: 'USD',
    subtotal: '3000.00',
    discount: '0.00',
    tax_total: '0.00',
    total: '3000.00',
    amount_paid: '3000.00',
    balance: '0.00',
    days_overdue: 0,
    notes: null,
    internal_notes: null,
    custom_fields: null,
    snapshot: null,
    share_token: null,
    schedule_id: 'sched-1',
    schedule: { id: 'sched-1', name: 'Plano Pro', frequency: 'monthly', status: 'active' },
    sequence: 1,
    period_start: '2026-01-05',
    period_end: '2026-02-04',
    lines: [],
    allocations: [],
    created_at: '2026-01-05T00:00:00Z',
    ...overrides,
  }
}

function render() {
  return renderWithProviders(<InvoiceScheduleDetailPage />, {
    route: '/invoices/schedules/sched-1',
    path: '/invoices/schedules/:id',
  })
}

describe('InvoiceScheduleDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    canWrite = true
    api.invoiceSchedules.get.mockResolvedValue(schedule())
    api.invoiceSchedules.invoices.mockResolvedValue([
      invoice(),
      invoice({ id: 'inv-2', number: 13, state: 'overdue', days_overdue: 20, sequence: 2,
        period_start: '2026-02-05', period_end: '2026-03-04', due_date: '2026-02-15', amount_paid: '0.00', balance: '3000.00' }),
    ])
    api.invoiceSchedules.periods.mockResolvedValue([])
    api.invoices.settings.mockResolvedValue({ default_payment_terms_days: 30, tax_fields: 'hidden', number_prefix: 'INV-' })
    api.invoices.list.mockResolvedValue([])
    api.payees.list.mockResolvedValue([])
  })

  it('shows the derived figures and flags the late invoice', async () => {
    render()
    expect(await screen.findByText('Plano Pro')).toBeInTheDocument()
    expect(screen.getByText('$6,000.00')).toBeInTheDocument()
    expect(screen.getByText(t('invoices.schedules.figure.pastDue', { count: 1 }))).toBeInTheDocument()
    expect(screen.getByTestId('schedule-state-past_due')).toBeInTheDocument()
  })

  it('lists the price history with the current and the upcoming term', async () => {
    render()
    await screen.findByText('Plano Pro')
    const rows = within(screen.getByTestId('schedule-terms')).getAllByTestId('schedule-term-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText(t('invoices.schedules.terms.current'))).toBeInTheDocument()
    expect(within(rows[0]).getByText('$3,000.00')).toBeInTheDocument()
    expect(within(rows[1]).getByText(t('invoices.schedules.terms.upcoming'))).toBeInTheDocument()
    expect(within(rows[1]).getByText('$3,500.00')).toBeInTheDocument()
    // Only the upcoming term may be touched; the current one is history.
    expect(within(rows[0]).queryByLabelText(t('common.edit'))).not.toBeInTheDocument()
    expect(within(rows[1]).getByLabelText(t('common.edit'))).toBeInTheDocument()
  })

  it('lists every invoice by its period, newest first', async () => {
    render()
    await screen.findByText('Plano Pro')
    const rows = screen.getAllByTestId('schedule-invoice-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('INV-13')).toBeInTheDocument()
    expect(within(rows[0]).getByTestId('invoice-state-overdue')).toBeInTheDocument()
    expect(within(rows[1]).getByText('INV-12')).toBeInTheDocument()
    expect(within(rows[1]).getByText(/Jan 5/)).toBeInTheDocument()
  })

  it('offers the actions an active agreement has, and hides them from a viewer', async () => {
    const { user } = render()
    await screen.findByText('Plano Pro')
    expect(screen.getByTestId('schedule-generate')).toBeInTheDocument()
    expect(screen.getByTestId('schedule-change-price')).toBeInTheDocument()
    await user.click(screen.getByTestId('schedule-more-actions'))
    expect(await screen.findByTestId('schedule-pause')).toBeInTheDocument()
    expect(screen.getByTestId('schedule-end')).toBeInTheDocument()
    // Two invoices were issued under it, so it cannot be deleted.
    expect(screen.queryByTestId('schedule-delete')).not.toBeInTheDocument()
  })

  it('shows nothing to press to a viewer', async () => {
    canWrite = false
    render()
    await screen.findByText('Plano Pro')
    expect(screen.queryByTestId('schedule-generate')).not.toBeInTheDocument()
    expect(screen.queryByTestId('schedule-more-actions')).not.toBeInTheDocument()
    expect(screen.queryByTestId('schedule-link-invoice')).not.toBeInTheDocument()
  })

  it('reads an ended agreement as history: when, why, and nothing to emit', async () => {
    api.invoiceSchedules.get.mockResolvedValue(
      schedule({ status: 'ended', ended_at: '2026-09-01', end_reason: 'canceled_by_client', next_period_start: null, monthly_amount: '0.00' }),
    )
    render()
    await screen.findByText('Plano Pro')
    expect(screen.getByTestId('schedule-state-ended')).toBeInTheDocument()
    expect(screen.getByTestId('schedule-ended-line')).toHaveTextContent(t('invoices.schedules.endReason.canceled_by_client'))
    expect(screen.queryByTestId('schedule-generate')).not.toBeInTheDocument()
    expect(screen.queryByTestId('schedule-change-price')).not.toBeInTheDocument()
    expect(screen.queryByTestId('schedule-more-actions')).not.toBeInTheDocument()
  })

  it('warns loudly when the job paused the agreement after failures', async () => {
    api.invoiceSchedules.get.mockResolvedValue(schedule({ status: 'paused', pause_reason: 'failures' }))
    render()
    await screen.findByText('Plano Pro')
    expect(screen.getByTestId('schedule-state-paused_failures')).toBeInTheDocument()
    expect(screen.getByText(t('invoices.schedules.pausedByFailures'))).toBeInTheDocument()
  })

  it('records the price change with the next period as the default date', async () => {
    api.invoiceSchedules.addTerm.mockResolvedValue(schedule())
    const { user } = render()
    await screen.findByText('Plano Pro')
    await user.click(screen.getByTestId('schedule-change-price'))
    expect(await screen.findByTestId('term-from-input')).toHaveValue('2026-03-05')
    await user.clear(screen.getByTestId('invoice-line-price-0'))
    await user.type(screen.getByTestId('invoice-line-price-0'), '3200')
    expect(screen.getByTestId('term-monthly-preview')).toHaveTextContent('$3,200.00')
    await user.click(screen.getByTestId('term-save'))
    expect(api.invoiceSchedules.addTerm).toHaveBeenCalledWith('sched-1', {
      effective_from: '2026-03-05',
      lines: [{ description: 'Retainer', quantity: '1', unit_price: '3200' }],
      discount: '0.00',
    })
  })
})
