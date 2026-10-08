import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import type { AxiosError } from 'axios'

import { TwoFactorSetup } from '@/components/two-factor-setup'
import { renderWithProviders, t } from '@/test/utils'

const authContext = vi.hoisted(() => ({
  user: { is_2fa_enabled: true },
  updateUser: vi.fn(),
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => authContext }))

const api = vi.hoisted(() => ({ auth: { disable2fa: vi.fn() } }))
vi.mock('@/lib/api', () => ({ auth: api.auth }))

function detailError(detail: string): AxiosError<{ detail: string }> {
  return {
    isAxiosError: true,
    response: { status: 400, data: { detail } },
  } as AxiosError<{ detail: string }>
}

async function submitDisable() {
  const { user } = renderWithProviders(<TwoFactorSetup open onClose={vi.fn()} />)
  await user.type(screen.getByLabelText(t('auth.password')), 'wrong-password')
  await user.type(screen.getByLabelText(t('auth.twoFactor')), '123456')
  await user.click(screen.getByRole('button', { name: t('auth.disable2fa') }))
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TwoFactorSetup disable flow', () => {
  it('blames the password when the backend rejected the password', async () => {
    api.auth.disable2fa.mockRejectedValue(detailError('Invalid password'))

    await submitDisable()

    expect(await screen.findByRole('alert')).toHaveTextContent(t('auth.currentPasswordWrong'))
    expect(screen.queryByText(t('auth.invalid2faCode'))).not.toBeInTheDocument()
  })

  it('blames the connection when the server never answered', async () => {
    api.auth.disable2fa.mockRejectedValue({ isAxiosError: true } as AxiosError)

    await submitDisable()

    expect(await screen.findByRole('alert')).toHaveTextContent(t('auth.serverError'))
  })
})
