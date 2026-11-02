import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { PostTodayButton } from '@/components/post-today-button'
import { renderWithProviders, t } from '@/test/utils'

const pending = { id: 'tx-1', status: 'pending', source: 'manual' }
const posted = { id: 'tx-2', status: 'posted', source: 'manual' }
const syncPending = { id: 'tx-3', status: 'pending', source: 'sync' }

describe('PostTodayButton', () => {
  it('offers the action on a pending row the user can edit', () => {
    const onPostToday = vi.fn()
    renderWithProviders(<PostTodayButton {...pending} canWrite onPostToday={onPostToday} />)

    const button = screen.getByRole('button', { name: t('transactions.postToday') })
    expect(button).toBeInTheDocument()

    button.click()
    expect(onPostToday).toHaveBeenCalledTimes(1)
    expect(onPostToday).toHaveBeenCalledWith('tx-1')
  })

  it('is hidden once the transaction is posted', () => {
    renderWithProviders(<PostTodayButton {...posted} canWrite onPostToday={vi.fn()} />)

    expect(
      screen.queryByRole('button', { name: t('transactions.postToday') }),
    ).not.toBeInTheDocument()
  })

  it('is hidden for a pending row that came from a bank sync', () => {
    renderWithProviders(<PostTodayButton {...syncPending} canWrite onPostToday={vi.fn()} />)

    expect(
      screen.queryByRole('button', { name: t('transactions.postToday') }),
    ).not.toBeInTheDocument()
  })

  it('is hidden for a read-only user', () => {
    renderWithProviders(<PostTodayButton {...pending} canWrite={false} onPostToday={vi.fn()} />)

    expect(
      screen.queryByRole('button', { name: t('transactions.postToday') }),
    ).not.toBeInTheDocument()
  })

  it('renders translated copy, not the raw key', () => {
    renderWithProviders(<PostTodayButton {...pending} canWrite onPostToday={vi.fn()} />)

    expect(screen.queryByText('transactions.postToday')).not.toBeInTheDocument()
  })
})
