import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import GoalsPage from '@/pages/goals'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  goals: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  accounts: { list: vi.fn() },
  assets: { list: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  goals: api.goals,
  accounts: api.accounts,
  assets: api.assets,
  assetGroups: api.assetGroups,
  currencies: api.currencies,
}))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: true, current: { id: 'ws-1' } }) }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.goals.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([])
  api.assets.list.mockResolvedValue([])
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
})

afterEach(() => vi.restoreAllMocks())

describe('goal form labels', () => {
  it('names the icon picker after its label and its visible text', async () => {
    const { user } = renderWithProviders(<GoalsPage />)

    await user.click(await screen.findByRole('button', { name: t('goals.add') }))

    // The icon trigger is a <button>; a <label for> pointing at it does not name
    // it. It must say which field it is and what the button itself says.
    expect(await screen.findByRole('button', {
      name: `${t('goals.icon')} ${t('goals.chooseIconColor')}`,
    })).toBeInTheDocument()
  })

  it('focuses the field when its label is clicked', async () => {
    const { user } = renderWithProviders(<GoalsPage />)

    await user.click(await screen.findByRole('button', { name: t('goals.add') }))

    const nameField = screen.getByLabelText(t('goals.name'))
    await user.click(screen.getByText(t('goals.name')))
    expect(nameField).toHaveFocus()
  })
})
