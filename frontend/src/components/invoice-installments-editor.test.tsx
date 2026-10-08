import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { InvoiceInstallmentsEditor } from '@/components/invoice-installments-editor'
import { renderWithProviders, t } from '@/test/utils'
import type { InstallmentInput } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

function Harness({ total = 3000, initial = null }: { total?: number; initial?: InstallmentInput[] | null }) {
  const [value, setValue] = useState<InstallmentInput[] | null>(initial)
  return (
    <InvoiceInstallmentsEditor
      value={value}
      onChange={setValue}
      total={total}
      currency="USD"
      firstDueDate="2026-10-01"
      minDate="2026-09-01"
    />
  )
}

describe('InvoiceInstallmentsEditor', () => {
  it('is one switch, off, until asked', () => {
    renderWithProviders(<Harness />)
    expect(screen.getByRole('switch')).toBeInTheDocument()
    expect(screen.queryByTestId('installment-row')).not.toBeInTheDocument()
  })

  it('seeds two equal installments a month apart when switched on', async () => {
    const { user } = renderWithProviders(<Harness />)
    await user.click(screen.getByRole('switch'))
    const rows = screen.getAllByTestId('installment-row')
    expect(rows).toHaveLength(2)
    expect(screen.getByTestId('installment-due-0')).toHaveValue('2026-10-01')
    expect(screen.getByTestId('installment-due-1')).toHaveValue('2026-11-01')
    expect(screen.getByTestId('installment-amount-0')).toHaveValue('1500.00')
    expect(screen.getByTestId('installments-check')).toHaveTextContent(t('invoices.installments.addsUp', { total: '$3,000.00' }))
  })

  it('shows the mismatch, to the cent, as the amounts are edited', async () => {
    const { user } = renderWithProviders(<Harness />)
    await user.click(screen.getByRole('switch'))
    await user.clear(screen.getByTestId('installment-amount-1'))
    await user.type(screen.getByTestId('installment-amount-1'), '1499.99')
    expect(screen.getByTestId('installments-check')).toHaveTextContent(
      t('invoices.installments.mismatch', { scheduled: '$2,999.99', total: '$3,000.00' }),
    )
  })

  it('adds a row a month after the last one and never drops below two', async () => {
    const { user } = renderWithProviders(<Harness />)
    await user.click(screen.getByRole('switch'))
    await user.click(screen.getByTestId('installments-add'))
    expect(screen.getAllByTestId('installment-row')).toHaveLength(3)
    expect(screen.getByTestId('installment-due-2')).toHaveValue('2026-12-01')
    await user.click(screen.getByTestId('installment-remove-2'))
    expect(screen.getAllByTestId('installment-row')).toHaveLength(2)
    expect(screen.getByTestId('installment-remove-0')).toBeDisabled()
  })

  it('switching off clears the schedule', async () => {
    const { user } = renderWithProviders(<Harness initial={[{ due_date: '2026-10-01', amount: '1500.00' }, { due_date: '2026-11-01', amount: '1500.00' }]} />)
    expect(screen.getAllByTestId('installment-row')).toHaveLength(2)
    await user.click(screen.getByRole('switch'))
    expect(screen.queryByTestId('installment-row')).not.toBeInTheDocument()
  })
})
