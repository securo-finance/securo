import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PageRefreshButton } from '@/components/page-refresh-button'
import { renderWithProviders } from '@/test/utils'

describe('PageRefreshButton', () => {
  it('refetches when clicked', async () => {
    const onRefresh = vi.fn()

    const { user } = renderWithProviders(<PageRefreshButton onRefresh={onRefresh} />)

    const button = screen.getByRole('button', { name: 'Refresh' })
    await user.click(button)

    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('spins and disables while a refetch is in flight', () => {
    renderWithProviders(
      <PageRefreshButton onRefresh={() => {}} refreshing />,
    )

    const button = screen.getByRole('button', { name: 'Refresh' })
    expect(button).toBeDisabled()
    expect(button.querySelector('.animate-spin')).not.toBeNull()
  })

  it('stays busy until the refetch it started settles', async () => {
    let settle: () => void = () => {}
    const onRefresh = vi.fn(() => new Promise<void>((resolve) => { settle = resolve }))

    const { user } = renderWithProviders(<PageRefreshButton onRefresh={onRefresh} />)
    const button = screen.getByRole('button', { name: 'Refresh' })
    await user.click(button)

    expect(button).toBeDisabled()
    settle()
    await waitFor(() => expect(button).toBeEnabled())
  })
})
