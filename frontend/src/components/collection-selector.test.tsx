import { screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import { CollectionSelector } from '@/components/collection-selector'
import { renderWithProviders, t } from '@/test/utils'

const filter = vi.hoisted(() => ({ collections: [] as unknown[], activeCollection: null as unknown, setActiveCollectionId: vi.fn() }))
vi.mock('@/contexts/collection-filter-context', () => ({ useCollectionFilter: () => filter }))

const TRIP = { id: 'c1', name: 'Trip', color: '#ff0000', account_count: 2, wallet_count: 0 }

beforeEach(() => {
  filter.collections = []
  filter.activeCollection = null
  filter.setActiveCollectionId.mockReset()
})

it('is there without collections, and leads to making one', async () => {
  const { user } = renderWithProviders(<CollectionSelector />)
  await user.click(screen.getByRole('button', { name: t('collections.allAccounts') }))
  expect(screen.getByRole('menuitem', { name: t('collections.manage') })).toBeInTheDocument()
})

it('picks a collection and clears it', async () => {
  filter.collections = [TRIP]
  const { user, rerender } = renderWithProviders(<CollectionSelector />)
  await user.click(screen.getByRole('button', { name: t('collections.allAccounts') }))
  await user.click(screen.getByRole('menuitem', { name: /Trip/ }))
  expect(filter.setActiveCollectionId).toHaveBeenCalledWith('c1')

  filter.activeCollection = TRIP
  rerender(<CollectionSelector />)
  await user.click(screen.getByRole('button', { name: t('collections.clearFilter') }))
  expect(filter.setActiveCollectionId).toHaveBeenLastCalledWith(null)
})
