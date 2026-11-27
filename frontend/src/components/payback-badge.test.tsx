import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { PaybackBadge } from '@/components/payback-badge'
import { renderWithProviders, t } from '@/test/utils'

describe('PaybackBadge', () => {
  it('labels a credit that pays back a group debt', () => {
    renderWithProviders(
      <PaybackBadge tx={{ settled_by: [{ id: 's-1', group_id: 'g-1', from_member_id: 'm-1', amount: 30 }] }} />,
    )

    expect(screen.getByText(t('splitGroups.shareSettlement'))).toBeInTheDocument()
  })

  it('renders nothing when the credit settles nothing', () => {
    renderWithProviders(<PaybackBadge tx={{ settled_by: [] }} />)

    expect(screen.queryByText(t('splitGroups.shareSettlement'))).not.toBeInTheDocument()
  })
})
