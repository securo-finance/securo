import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import type { AxiosError } from 'axios'

import ResetPasswordPage from '@/pages/reset-password'
import { renderWithProviders, t } from '@/test/utils'

const authApi = vi.hoisted(() => ({ resetPassword: vi.fn() }))
vi.mock('@/lib/api', () => ({ auth: authApi }))

function httpError(status: number, detail?: unknown): AxiosError {
  return {
    isAxiosError: true,
    response: { status, data: { detail } },
  } as AxiosError
}

beforeEach(() => {
  vi.clearAllMocks()
})

async function fillAndSubmit(user: ReturnType<typeof renderWithProviders>['user']) {
  await user.type(screen.getByLabelText(t('auth.newPassword')), 'brandnewpass1')
  await user.type(screen.getByLabelText(t('auth.confirmPassword')), 'brandnewpass1')
  await user.click(screen.getByRole('button', { name: t('auth.resetPassword') }))
}

describe('ResetPasswordPage', () => {
  it('sends the token from the link with the new password', async () => {
    authApi.resetPassword.mockResolvedValue(undefined)
    const { user } = renderWithProviders(<ResetPasswordPage />, {
      route: '/reset-password?token=tok123',
    })

    await fillAndSubmit(user)

    expect(authApi.resetPassword).toHaveBeenCalledWith('tok123', 'brandnewpass1')
    expect(await screen.findByText(t('auth.passwordChanged'))).toBeInTheDocument()
  })

  it('stops before the server when the two passwords differ', async () => {
    const { user } = renderWithProviders(<ResetPasswordPage />, {
      route: '/reset-password?token=tok123',
    })

    await user.type(screen.getByLabelText(t('auth.newPassword')), 'brandnewpass1')
    await user.type(screen.getByLabelText(t('auth.confirmPassword')), 'different1')
    await user.click(screen.getByRole('button', { name: t('auth.resetPassword') }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('auth.passwordMismatch'),
    )
    expect(authApi.resetPassword).not.toHaveBeenCalled()
  })

  it('says an expired link is expired', async () => {
    authApi.resetPassword.mockRejectedValue(httpError(400, 'RESET_PASSWORD_BAD_TOKEN'))
    const { user } = renderWithProviders(<ResetPasswordPage />, {
      route: '/reset-password?token=stale',
    })

    await fillAndSubmit(user)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('auth.resetTokenInvalid'),
    )
  })

  it('refuses a password past the length the server accepts', async () => {
    const { user } = renderWithProviders(<ResetPasswordPage />, {
      route: '/reset-password?token=tok123',
    })

    // bcrypt stops at 72 bytes, and the route refuses anything longer.
    const tooLong = 'a'.repeat(73)
    await user.type(screen.getByLabelText(t('auth.newPassword')), tooLong)
    await user.type(screen.getByLabelText(t('auth.confirmPassword')), tooLong)
    await user.click(screen.getByRole('button', { name: t('auth.resetPassword') }))

    expect(await screen.findByRole('alert')).toHaveTextContent(t('auth.passwordTooLong'))
    expect(authApi.resetPassword).not.toHaveBeenCalled()
  })

  it('asks for a fresh link when the address has no token', () => {
    renderWithProviders(<ResetPasswordPage />, { route: '/reset-password' })

    expect(screen.getByRole('alert')).toHaveTextContent(t('auth.resetTokenMissing'))
    expect(screen.queryByLabelText(t('auth.newPassword'))).not.toBeInTheDocument()
  })
})
