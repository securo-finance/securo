import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import type { AxiosError } from 'axios'

import ForgotPasswordPage from '@/pages/forgot-password'
import { renderWithProviders, t } from '@/test/utils'

const authApi = vi.hoisted(() => ({ forgotPassword: vi.fn() }))
vi.mock('@/lib/api', () => ({ auth: authApi }))

function httpError(status?: number, detail?: unknown): AxiosError {
  return (status === undefined
    ? { isAxiosError: true, response: undefined }
    : { isAxiosError: true, response: { status, data: { detail } } }) as AxiosError
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ForgotPasswordPage', () => {
  it('asks for the link and says the message is on its way', async () => {
    authApi.forgotPassword.mockResolvedValue(undefined)
    const { user } = renderWithProviders(<ForgotPasswordPage />, {
      route: '/forgot-password',
    })

    await user.type(screen.getByLabelText(t('auth.email')), 'someone@example.com')
    await user.click(screen.getByRole('button', { name: t('auth.sendResetLink') }))

    expect(authApi.forgotPassword).toHaveBeenCalledWith('someone@example.com')
    expect(await screen.findByText(t('auth.resetSentTitle'))).toBeInTheDocument()
    // Whichever address it was, the screen does not claim an account exists.
    expect(
      screen.getByText(t('auth.resetSentBody', { email: 'someone@example.com' })),
    ).toBeInTheDocument()
  })

  it('admits the instance has no email instead of a fake success', async () => {
    // #540: the route answers 503 while EMAIL_ENABLED is off.
    authApi.forgotPassword.mockRejectedValue(
      httpError(503, 'Password recovery by email is not enabled on this instance'),
    )
    const { user } = renderWithProviders(<ForgotPasswordPage />, {
      route: '/forgot-password',
    })

    await user.type(screen.getByLabelText(t('auth.email')), 'someone@example.com')
    await user.click(screen.getByRole('button', { name: t('auth.sendResetLink') }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('auth.resetUnavailable'),
    )
    expect(screen.queryByText(t('auth.resetSentTitle'))).not.toBeInTheDocument()
  })

  it('honours the rate limit', async () => {
    authApi.forgotPassword.mockRejectedValue(httpError(429))
    const { user } = renderWithProviders(<ForgotPasswordPage />, {
      route: '/forgot-password',
    })

    await user.type(screen.getByLabelText(t('auth.email')), 'someone@example.com')
    await user.click(screen.getByRole('button', { name: t('auth.sendResetLink') }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('auth.tooManyAttempts'),
    )
  })
})
