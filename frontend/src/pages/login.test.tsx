import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import type { AxiosError } from 'axios'

import LoginPage from '@/pages/login'
import { renderWithProviders, t } from '@/test/utils'

const navigate = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}))

const authContext = vi.hoisted(() => ({
  login: vi.fn(),
  verify2fa: vi.fn(),
  loginWithToken: vi.fn(),
  token: null as string | null,
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => authContext }))

const api = vi.hoisted(() => ({
  setup: { status: vi.fn() },
  auth: { oidcConfig: vi.fn() },
  admin: { registrationStatus: vi.fn(), defaultColors: vi.fn() },
}))
vi.mock('@/lib/api', () => ({
  setup: api.setup,
  auth: api.auth,
  admin: api.admin,
}))

vi.mock('@/lib/webauthn', () => ({
  isPasskeySupported: () => false,
  passkeyFailure: () => 'unknown',
  startPasskeyAuthentication: vi.fn(),
}))

vi.mock('next-themes', () => ({ useTheme: () => ({ resolvedTheme: 'dark' }) }))

/** Build the axios-shaped rejection the page branches on. */
function httpError(status?: number): AxiosError {
  return (status === undefined
    ? { isAxiosError: true, response: undefined }
    : { isAxiosError: true, response: { status } }) as AxiosError
}

beforeEach(() => {
  vi.clearAllMocks()
  authContext.token = null
  api.setup.status.mockResolvedValue({ has_users: true })
  api.auth.oidcConfig.mockResolvedValue({
    enabled: false,
    provider_name: 'OIDC',
    local_auth_enabled: true,
  })
  api.admin.registrationStatus.mockResolvedValue({ enabled: true })
  api.admin.defaultColors.mockResolvedValue({ light: null, dark: null })
})

async function renderLogin() {
  const rendered = renderWithProviders(<LoginPage />, { route: '/login' })
  await screen.findByLabelText(t('auth.email'))
  return rendered
}

describe('LoginPage', () => {
  it('renders the credentials form', async () => {
    await renderLogin()

    expect(screen.getByLabelText(t('auth.email'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('auth.password'))).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: t('auth.login') }),
    ).toBeInTheDocument()
  })

  it('signs in and lands on the dashboard', async () => {
    authContext.login.mockResolvedValue({ requires_2fa: false })
    const { user } = await renderLogin()

    await user.type(screen.getByLabelText(t('auth.email')), 'tassio@example.com')
    await user.type(screen.getByLabelText(t('auth.password')), 'secret')
    await user.click(screen.getByRole('button', { name: t('auth.login') }))

    await waitFor(() =>
      expect(authContext.login).toHaveBeenCalledWith(
        'tassio@example.com',
        'secret',
      ),
    )
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/'))
  })

  it('asks for the second factor instead of navigating when 2FA is on', async () => {
    authContext.login.mockResolvedValue({
      requires_2fa: true,
      temp_token: 'temp',
      available_methods: ['totp'],
    })
    const { user } = await renderLogin()

    await user.type(screen.getByLabelText(t('auth.email')), 'tassio@example.com')
    await user.type(screen.getByLabelText(t('auth.password')), 'secret')
    await user.click(screen.getByRole('button', { name: t('auth.login') }))

    expect(await screen.findByText(t('auth.twoFactorTitle'))).toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalledWith('/')
  })

  it('tells the user their credentials were rejected', async () => {
    authContext.login.mockRejectedValue(httpError(401))
    const { user } = await renderLogin()

    await user.type(screen.getByLabelText(t('auth.email')), 'a@b.com')
    await user.type(screen.getByLabelText(t('auth.password')), 'wrong')
    await user.click(screen.getByRole('button', { name: t('auth.login') }))

    expect(
      await screen.findByText(t('auth.invalidCredentials')),
    ).toBeInTheDocument()
  })

  it('distinguishes an outage from a wrong password', async () => {
    // Issue #318: collapsing every failure into "invalid credentials" made a
    // stopped backend look like the user's own mistake.
    authContext.login.mockRejectedValue(httpError())
    const { user } = await renderLogin()

    await user.type(screen.getByLabelText(t('auth.email')), 'a@b.com')
    await user.type(screen.getByLabelText(t('auth.password')), 'right')
    await user.click(screen.getByRole('button', { name: t('auth.login') }))

    expect(await screen.findByText(t('auth.serverError'))).toBeInTheDocument()
    expect(
      screen.queryByText(t('auth.invalidCredentials')),
    ).not.toBeInTheDocument()
  })

  it('reports a 5xx as an outage too', async () => {
    authContext.login.mockRejectedValue(httpError(502))
    const { user } = await renderLogin()

    await user.type(screen.getByLabelText(t('auth.email')), 'a@b.com')
    await user.type(screen.getByLabelText(t('auth.password')), 'right')
    await user.click(screen.getByRole('button', { name: t('auth.login') }))

    expect(await screen.findByText(t('auth.serverError'))).toBeInTheDocument()
  })

  it('names rate limiting rather than blaming the password', async () => {
    authContext.login.mockRejectedValue(httpError(429))
    const { user } = await renderLogin()

    await user.type(screen.getByLabelText(t('auth.email')), 'a@b.com')
    await user.type(screen.getByLabelText(t('auth.password')), 'right')
    await user.click(screen.getByRole('button', { name: t('auth.login') }))

    expect(await screen.findByText(t('auth.tooManyAttempts'))).toBeInTheDocument()
  })

  it('sends an already-signed-in visitor away from the login screen', async () => {
    authContext.token = 'jwt'

    renderWithProviders(<LoginPage />, { route: '/login' })

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('/', { replace: true }),
    )
  })

  it('redirects a fresh install to the setup wizard', async () => {
    // No users yet: sending someone to a login form they cannot pass is a
    // dead end.
    api.setup.status.mockResolvedValue({ has_users: false })

    renderWithProviders(<LoginPage />, { route: '/login' })

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('/setup', { replace: true }),
    )
  })

  it('offers registration when the server allows it', async () => {
    await renderLogin()

    expect(
      await screen.findByRole('link', { name: t('auth.register') }),
    ).toBeInTheDocument()
  })

  it('survives the optional config calls failing', async () => {
    // A reverse proxy that blocks /api/admin must not blank the login form.
    api.admin.registrationStatus.mockRejectedValue(new Error('403'))
    api.admin.defaultColors.mockRejectedValue(new Error('403'))
    api.auth.oidcConfig.mockRejectedValue(new Error('500'))

    await renderLogin()

    expect(screen.getByLabelText(t('auth.email'))).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: t('auth.login') }),
    ).toBeInTheDocument()
  })
})
