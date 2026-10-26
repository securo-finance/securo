import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { TransactionsPageActions } from '@/components/transactions-page-actions'
import { renderWithProviders } from '@/test/utils'

const base = {
  month: { value: '2026-10-01', onChange: () => {}, locale: 'en', prevLabel: 'Prev', nextLabel: 'Next' },
  view: { value: 'list' as const, onChange: () => {}, listLabel: 'List', calendarLabel: 'Calendar' },
  columnPicker: null,
  exportLabel: 'Export CSV',
  exporting: false,
  onExport: () => {},
}

describe('TransactionsPageActions refresh action', () => {
  it('shows the refresh action and refetches on click', async () => {
    const onRefresh = vi.fn()
    const { user } = renderWithProviders(
      <TransactionsPageActions {...base} onRefresh={onRefresh} />,
    )
    const button = screen.getByRole('button', { name: 'Refresh' })
    await user.click(button)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('disables the refresh action while a fetch is running', () => {
    renderWithProviders(
      <TransactionsPageActions {...base} onRefresh={() => {}} refreshing />,
    )
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeDisabled()
  })

  it('shows the refresh item in the mobile menu and refetches on click', async () => {
    const onRefresh = vi.fn()
    const { user } = renderWithProviders(
      <TransactionsPageActions {...base} onRefresh={onRefresh} />,
    )
    await user.click(screen.getByRole('button', { name: 'More actions' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Refresh' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('omits the refresh action when no handler is given', () => {
    renderWithProviders(<TransactionsPageActions {...base} />)
    expect(screen.queryByRole('button', { name: 'Refresh' })).toBeNull()
  })
})
