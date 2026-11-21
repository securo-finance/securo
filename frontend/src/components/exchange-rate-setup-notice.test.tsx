import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { ExchangeRateSetupIcon } from '@/components/exchange-rate-setup-notice'
import { renderWithProviders, t } from '@/test/utils'

describe('ExchangeRateSetupIcon', () => {
  it('keeps the screen-reader label anchored to the icon', () => {
    // The label is sr-only, which positions it absolutely. Its containing
    // block has to be the icon itself: otherwise it is laid out against the
    // page, outside the holdings table's horizontal scroll, and at 390 px
    // the whole Assets page scrolled sideways (scrollWidth 611).
    renderWithProviders(<ExchangeRateSetupIcon from="USD" to="BRL" isAdmin={false} />)

    const label = screen.getByText(t('assets.exchangeRateSetupAskAdmin', { from: 'USD', to: 'BRL' }))
    expect(label).toHaveClass('sr-only')
    expect(label.parentElement).toHaveClass('relative')
  })

  it('gives an admin a labelled button instead of hidden text', () => {
    renderWithProviders(<ExchangeRateSetupIcon from="USD" to="BRL" isAdmin />)

    expect(
      screen.getByRole('button', { name: t('assets.exchangeRateSetupAction', { from: 'USD', to: 'BRL' }) }),
    ).toBeInTheDocument()
  })
})
