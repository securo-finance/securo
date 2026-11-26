import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'

import { admin, currencies } from '@/lib/api'
import type { AdminUser } from '@/types'
import { i18n, renderWithProviders, t } from '@/test/utils'

import AdminSettingsPage from './settings'

vi.mock('@/lib/api', () => ({
  admin: {
    listUsers: vi.fn(), deleteUser: vi.fn(), registrationStatus: vi.fn(), getSetting: vi.fn(),
  },
  currencies: { list: vi.fn() },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { id: 'admin' } }) }))
vi.mock('@/hooks/use-local-auth', () => ({ useLocalAuthEnabled: () => true }))
vi.mock('@/components/timezone-settings', () => ({ TimezoneSettings: () => null }))
vi.mock('@/components/provider-settings', () => ({ ProviderSettings: () => null }))
vi.mock('@/components/email-settings', () => ({ EmailSettings: () => null }))

const person = {
  id: 'owner', email: 'owner@example.com', is_active: true, is_superuser: false,
} as AdminUser

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.listUsers).mockResolvedValue({ items: [person], total: 1 })
  vi.mocked(admin.registrationStatus).mockResolvedValue({ enabled: false })
  vi.mocked(admin.getSetting).mockRejectedValue(new Error('not set'))
  vi.mocked(currencies.list).mockResolvedValue([])
})

afterEach(async () => {
  await i18n.changeLanguage('en')
})

it('says why a deletion was refused in the language of the screen', async () => {
  await i18n.changeLanguage('pt-BR')
  vi.mocked(admin.deleteUser).mockRejectedValue({
    response: {
      data: {
        detail: { code: 'user_sole_owner', message: 'English', workspace: 'Ana & Co' },
      },
    },
  })
  const { user } = renderWithProviders(<AdminSettingsPage />)

  await user.click(await screen.findByTitle(t('common.delete')))
  const dialog = await screen.findByRole('dialog')
  // Every deletion carries its reason, so the dialog asks for one before it sends.
  await user.type(
    within(dialog).getByPlaceholderText(t('admin.users.reasonPlaceholder')),
    'Support ticket 12',
  )
  await user.click(within(dialog).getByRole('button', { name: t('common.delete') }))

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith(
      '"Ana & Co" tem outros membros e nenhum outro dono. Torne um deles dono, ou remova-os, antes de excluir este usuário.',
    ),
  )
})
