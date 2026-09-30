import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { InvoiceDocumentView } from '@/components/invoice-document'
import { renderWithProviders } from '@/test/utils'
import type { InvoiceDocumentPayload } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const LABELS = {
  invoice: 'Invoice', from: 'From', billTo: 'Bill to', issueDate: 'Issue date', dueDate: 'Due date',
  description: 'Description', quantity: 'Qty', unitPrice: 'Unit price', amount: 'Amount',
  subtotal: 'Subtotal', discount: 'Discount', tax: 'Tax', total: 'Total', paid: 'Paid',
  deducted: 'Deductions', balance: 'Balance due', paymentDetails: 'Payment details',
  notes: 'Notes', schedule: 'Payment schedule',
}

function payload(overrides: Partial<InvoiceDocumentPayload> = {}): InvoiceDocumentPayload {
  return {
    number: 'AUR22', status: 'open', state: 'partial', issue_date: '2026-09-24', due_date: '2026-10-24',
    currency: 'USD', subtotal: '3000.00', discount: '0.00', tax_total: '0.00', total: '3000.00',
    amount_paid: '0.00', amount_deducted: '0.00', balance: '3000.00',
    issuer: { name: 'Aurora Studio', address: null, tax_ids: [] },
    client: { name: 'Flux Studio', address: null, tax_ids: [] },
    lines: [{ description: 'Brand identity project', quantity: '1', unit: null, unit_price: '3000.00', total: '3000.00' }],
    labels: LABELS, accent_color: '#6d5efc', logo_url: null, payment_details: null, notes: null,
    footer_note: null, custom_fields: [], direction: 'receivable', has_line_items: true,
    source_file: null, installments: [],
    ...overrides,
  } as InvoiceDocumentPayload
}

function row(label: string) {
  return screen.getByText(label).parentElement as HTMLElement
}

describe('InvoiceDocumentView totals', () => {
  it('shows the deduction so total, paid and balance add up', () => {
    renderWithProviders(
      <InvoiceDocumentView document={payload({ amount_paid: '1455.00', amount_deducted: '45.00', balance: '1500.00' })} />,
    )
    expect(row('Paid')).toHaveTextContent('$1,455.00')
    expect(row('Deductions')).toHaveTextContent('$45.00')
    expect(row('Balance due')).toHaveTextContent('$1,500.00')
  })

  it('shows the balance when a deduction alone settled part of it', () => {
    renderWithProviders(<InvoiceDocumentView document={payload({ amount_deducted: '200.00', balance: '2800.00' })} />)
    expect(row('Deductions')).toHaveTextContent('$200.00')
    expect(row('Balance due')).toHaveTextContent('$2,800.00')
    expect(screen.queryByText('Paid')).not.toBeInTheDocument()
  })

  it('shows neither on an untouched invoice', () => {
    renderWithProviders(<InvoiceDocumentView document={payload()} />)
    expect(screen.queryByText('Deductions')).not.toBeInTheDocument()
    expect(screen.queryByText('Balance due')).not.toBeInTheDocument()
  })
})
