import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'

import { ProtectedRoute } from '@/components/protected-route'
import { renderWithProviders, t } from '@/test/utils'

const useAuth = vi.hoisted(() => vi.fn())
vi.mock('@/contexts/auth-context', () => ({ useAuth }))

function renderGuard() {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<div>login screen</div>} />
      <Route
        path="/private"
        element={
          <ProtectedRoute>
            <div>private page</div>
          </ProtectedRoute>
        }
      />
    </Routes>,
    { route: '/private' },
  )
}

describe('ProtectedRoute', () => {
  it('renders the page for an authenticated user', () => {
    useAuth.mockReturnValue({ token: 'jwt', user: null, isLoading: false })

    renderGuard()

    expect(screen.getByText('private page')).toBeInTheDocument()
  })

  it('sends an anonymous visitor to the login screen', () => {
    useAuth.mockReturnValue({ token: null, user: null, isLoading: false })

    renderGuard()

    expect(screen.getByText('login screen')).toBeInTheDocument()
    expect(screen.queryByText('private page')).not.toBeInTheDocument()
  })

  it('shows a loading indicator instead of redirecting while the session resolves', () => {
    // Redirecting during the initial `auth.me()` call would bounce every
    // returning user to /login on a hard refresh.
    useAuth.mockReturnValue({ token: 'jwt', user: null, isLoading: true })

    const { container } = renderGuard()

    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
    expect(screen.queryByText('private page')).not.toBeInTheDocument()
    expect(screen.queryByText('login screen')).not.toBeInTheDocument()
  })

  it('does not leak the page for a moment while loading without a token', () => {
    useAuth.mockReturnValue({ token: null, user: null, isLoading: true })

    renderGuard()

    expect(screen.queryByText('private page')).not.toBeInTheDocument()
  })

  it('keeps the requested URL and says the data is unavailable when the server cannot be reached', () => {
    // Issue #292: with the token kept but the session uncheckable, the app used
    // to render the shell and the dashboard with $0.00. This is an offline
    // state, not a sign-out: no login, and the URL is untouched.
    useAuth.mockReturnValue({
      token: 'jwt',
      user: null,
      isLoading: false,
      sessionUnreachable: true,
      retrySession: vi.fn(),
    })

    renderGuard()

    expect(screen.getByText(t('offline.title'))).toBeInTheDocument()
    expect(screen.getByText(t('offline.description'))).toBeInTheDocument()
    expect(screen.queryByText('login screen')).not.toBeInTheDocument()
    expect(screen.queryByText('private page')).not.toBeInTheDocument()
  })

  it('asks the provider to check the session again', async () => {
    const retrySession = vi.fn()
    useAuth.mockReturnValue({
      token: 'jwt',
      user: null,
      isLoading: false,
      sessionUnreachable: true,
      retrySession,
    })

    const { user } = renderGuard()
    await user.click(screen.getByRole('button', { name: t('common.retry') }))

    expect(retrySession).toHaveBeenCalledTimes(1)
  })
})
