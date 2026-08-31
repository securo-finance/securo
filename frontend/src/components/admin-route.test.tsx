import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'

import { AdminRoute } from '@/components/admin-route'
import { renderWithProviders } from '@/test/utils'

const useAuth = vi.hoisted(() => vi.fn())
vi.mock('@/contexts/auth-context', () => ({ useAuth }))

function renderGuard() {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<div>home</div>} />
      <Route
        path="/admin"
        element={
          <AdminRoute>
            <div>admin settings</div>
          </AdminRoute>
        }
      />
    </Routes>,
    { route: '/admin' },
  )
}

describe('AdminRoute', () => {
  it('renders for a superuser', () => {
    useAuth.mockReturnValue({
      token: 'jwt',
      user: { is_superuser: true },
      isLoading: false,
    })

    renderGuard()

    expect(screen.getByText('admin settings')).toBeInTheDocument()
  })

  it('sends a signed-in non-admin home', () => {
    // Hiding the nav link is not enough: the URL is still typeable.
    useAuth.mockReturnValue({
      token: 'jwt',
      user: { is_superuser: false },
      isLoading: false,
    })

    renderGuard()

    expect(screen.getByText('home')).toBeInTheDocument()
    expect(screen.queryByText('admin settings')).not.toBeInTheDocument()
  })

  it('sends an anonymous visitor home', () => {
    useAuth.mockReturnValue({ token: null, user: null, isLoading: false })

    renderGuard()

    expect(screen.getByText('home')).toBeInTheDocument()
  })

  it('treats a missing user object as not an admin', () => {
    useAuth.mockReturnValue({ token: 'jwt', user: null, isLoading: false })

    renderGuard()

    // Assert the redirect landed, not just that the page is absent: a guard
    // that rendered nothing at all would satisfy the weaker check while the
    // user stared at a blank /admin.
    expect(screen.getByText('home')).toBeInTheDocument()
    expect(screen.queryByText('admin settings')).not.toBeInTheDocument()
  })

  it('shows a loading indicator while the session resolves', () => {
    useAuth.mockReturnValue({ token: 'jwt', user: null, isLoading: true })

    const { container } = renderGuard()

    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
    expect(screen.queryByText('admin settings')).not.toBeInTheDocument()
    expect(screen.queryByText('home')).not.toBeInTheDocument()
  })
})
