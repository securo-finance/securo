import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { ProjectedTransactionBadge } from '@/components/projected-transaction-badge'
import { renderWithProviders, t } from '@/test/utils'

describe('ProjectedTransactionBadge', () => {
  it('labels a projected transaction from the bundle', () => {
    renderWithProviders(<ProjectedTransactionBadge />)

    expect(screen.getByText(t('transactions.projected'))).toBeInTheDocument()
  })

  it('renders translated copy, not the raw key', () => {
    // A missing key renders as "transactions.projected" in the UI, which is
    // the shape the Dutch locale regression took (#653).
    renderWithProviders(<ProjectedTransactionBadge />)

    expect(screen.queryByText('transactions.projected')).not.toBeInTheDocument()
  })
})
