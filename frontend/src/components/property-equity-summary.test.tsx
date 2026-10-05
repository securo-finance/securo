import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PropertyEquitySummary } from '@/components/property-equity-summary'
import { formatCurrency } from '@/lib/format'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Asset } from '@/types'

const home = { id: 'home', type: 'real_estate', current_value: 500000, current_value_primary: null } as Asset

function loan(id: string, name: string, balance: number, securedTo: string | null = 'home') {
  return {
    id, name, display_name: null, type: 'loan', secured_asset_id: securedTo,
    current_balance: balance, balance_primary: null, currency: 'EUR',
  } as unknown as Account
}

const props = { currency: 'EUR', locale: 'en-US', mask: (value: string) => value }

describe('PropertyEquitySummary', () => {
  it('lists the linked loans with the debt and the equity left', () => {
    renderWithProviders(
      <PropertyEquitySummary
        asset={home}
        loans={[loan('a', 'Part A', -200000), loan('b', 'Part B', -50000), loan('c', 'Elsewhere', -9000, 'other')]}
        {...props}
      />,
    )

    expect(screen.getByText('Part A')).toBeInTheDocument()
    expect(screen.getByText('Part B')).toBeInTheDocument()
    expect(screen.queryByText('Elsewhere')).not.toBeInTheDocument()
    expect(screen.getByText(t('assets.propertyEquity', {
      value: formatCurrency(500000, 'EUR', 'en-US'),
      debt: formatCurrency(250000, 'EUR', 'en-US'),
      equity: formatCurrency(250000, 'EUR', 'en-US'),
    }))).toBeInTheDocument()
  })

  it('shows nothing for a property without a linked loan', () => {
    const { container } = renderWithProviders(
      <PropertyEquitySummary asset={home} loans={[loan('c', 'Elsewhere', -9000, 'other')]} {...props} />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})
