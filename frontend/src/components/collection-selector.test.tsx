import { screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import { CollectionSelector } from '@/components/collection-selector'
import { CollectionFilterProvider } from '@/contexts/collection-filter-provider'
import { renderWithProviders, t } from '@/test/utils'

const navigate = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  collections: { list: async () => state.collections },
}))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ current: { id: 'ws-1' } }) }))

const state = vi.hoisted(() => ({ collections: [] as unknown[] }))

const TRIP = { id: 'c1', name: 'Trip', color: '#ff0000', account_ids: [], wallet_ids: [], account_count: 2, wallet_count: 0 }

beforeEach(() => {
  state.collections = []
  localStorage.clear()
})

function setup() {
  return renderWithProviders(
    <CollectionFilterProvider>
      <CollectionSelector />
    </CollectionFilterProvider>,
  )
}

it('is there without collections, and leads to making one', async () => {
  const { user } = setup()
  await user.click(screen.getByRole('button', { name: t('collections.allAccounts') }))
  await user.click(screen.getByRole('menuitem', { name: t('collections.manage') }))
  expect(navigate).toHaveBeenCalledWith('/collections')
})

it('shows the collection picked, and all accounts once cleared', async () => {
  state.collections = [TRIP]
  const { user } = setup()
  await user.click(screen.getByRole('button', { name: t('collections.allAccounts') }))
  await user.click(await screen.findByRole('menuitem', { name: /Trip/ }))
  expect(await screen.findByRole('button', { name: 'Trip' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: t('collections.allAccounts') })).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: t('collections.clearFilter') }))
  expect(await screen.findByRole('button', { name: t('collections.allAccounts') })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Trip' })).not.toBeInTheDocument()
})
