import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { Route, Routes, useLocation } from 'react-router-dom'

import { ProtectedRoute } from '@/components/protected-route'
import { UNREACHABLE_RETRY_MS } from '@/components/server-unreachable'
import i18n from '@/lib/i18n'
import { renderWithProviders } from '@/test/utils'

const useAuth = vi.hoisted(() => vi.fn())
vi.mock('@/contexts/auth-context', () => ({ useAuth }))

const useWorkspace = vi.hoisted(() => vi.fn())
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace }))

const retrySession = vi.fn()
const refresh = vi.fn(() => Promise.resolve())

function auth(overrides: Record<string, unknown>) {
  useAuth.mockReturnValue({
    token: 'jwt',
    user: null,
    isLoading: false,
    isUnreachable: false,
    retrySession,
    ...overrides,
  })
}

function workspace(overrides: Record<string, unknown> = {}) {
  useWorkspace.mockReturnValue({ loadFailed: false, refresh, ...overrides })
}

function CurrentPath() {
  return <div data-testid="path">{useLocation().pathname}</div>
}

function renderGuard(route = '/private') {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/" element={<div>home dashboard</div>} />
        <Route path="/login" element={<div>login screen</div>} />
        <Route
          path="/private"
          element={
            <ProtectedRoute>
              <div>private page</div>
            </ProtectedRoute>
          }
        />
      </Routes>
      <CurrentPath />
    </>,
    { route },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace()
})

afterEach(async () => {
  vi.useRealTimers()
  await i18n.changeLanguage('en')
})

describe('ProtectedRoute', () => {
  it('renders the page for an authenticated user', () => {
    auth({ user: { id: 'u1' } })

    renderGuard()

    expect(screen.getByText('private page')).toBeInTheDocument()
  })

  it('sends an anonymous visitor to the login screen', () => {
    auth({ token: null })

    renderGuard()

    expect(screen.getByText('login screen')).toBeInTheDocument()
    expect(screen.queryByText('private page')).not.toBeInTheDocument()
  })

  it('shows a loading indicator instead of redirecting while the session resolves', () => {
    // Redirecting during the initial `auth.me()` call would bounce every
    // returning user to /login on a hard refresh.
    auth({ isLoading: true })

    const { container } = renderGuard()

    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
    expect(screen.queryByText('private page')).not.toBeInTheDocument()
    expect(screen.queryByText('login screen')).not.toBeInTheDocument()
  })

  it('does not leak the page for a moment while loading without a token', () => {
    auth({ token: null, isLoading: true })

    renderGuard()

    expect(screen.queryByText('private page')).not.toBeInTheDocument()
  })
})

describe('ProtectedRoute when the server cannot be reached (#292)', () => {
  it('says so instead of rendering the page, and keeps the URL', () => {
    auth({ isUnreachable: true })

    renderGuard('/private')

    expect(screen.getByRole('alert')).toHaveTextContent("Can't reach the server")
    expect(screen.queryByText('private page')).not.toBeInTheDocument()
    expect(screen.queryByText('home dashboard')).not.toBeInTheDocument()
    expect(screen.queryByText('login screen')).not.toBeInTheDocument()
    expect(screen.getByTestId('path')).toHaveTextContent('/private')
  })

  it('also covers a session whose workspace list failed to load', () => {
    auth({ user: { id: 'u1' } })
    workspace({ loadFailed: true })

    renderGuard()

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText('private page')).not.toBeInTheDocument()
  })

  it('opens the page asked for once the session loads', () => {
    auth({ isUnreachable: true })
    const { rerender } = renderGuard()

    auth({ user: { id: 'u1' } })
    rerender(
      <Routes>
        <Route path="/private" element={<ProtectedRoute><div>private page</div></ProtectedRoute>} />
      </Routes>,
    )

    expect(screen.getByText('private page')).toBeInTheDocument()
  })

  it('retries the session from the button and on a timer', () => {
    vi.useFakeTimers()
    auth({ isUnreachable: true })
    renderGuard()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retrySession).toHaveBeenCalledTimes(1)

    act(() => { vi.advanceTimersByTime(UNREACHABLE_RETRY_MS) })
    expect(retrySession).toHaveBeenCalledTimes(2)
    expect(refresh).not.toHaveBeenCalled()
  })

  it('retries the workspace list when only that failed', () => {
    auth({ user: { id: 'u1' } })
    workspace({ loadFailed: true })
    renderGuard()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(retrySession).not.toHaveBeenCalled()
  })

  it('speaks the language the app was last used in', async () => {
    await i18n.changeLanguage('pt-BR')
    auth({ isUnreachable: true })

    renderGuard()

    expect(screen.getByRole('alert')).toHaveTextContent('Não foi possível conectar ao servidor')
    expect(screen.getByRole('button', { name: 'Tentar de novo' })).toBeInTheDocument()
  })
})
