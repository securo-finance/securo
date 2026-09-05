import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'

import { ModuleRoute } from '@/components/module-route'
import { renderWithProviders } from '@/test/utils'

const useWorkspace = vi.hoisted(() => vi.fn())
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace }))

function renderGuard() {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<div>home</div>} />
      <Route
        path="/budgets"
        element={
          <ModuleRoute module="budgets">
            <div>budgets page</div>
          </ModuleRoute>
        }
      />
    </Routes>,
    { route: '/budgets' },
  )
}

describe('ModuleRoute', () => {
  it('renders the page when the workspace has the module', () => {
    useWorkspace.mockReturnValue({ hasModule: () => true, isLoading: false })

    renderGuard()

    expect(screen.getByText('budgets page')).toBeInTheDocument()
  })

  it('sends the user home when the workspace does not have it', () => {
    // Hiding the nav entry leaves the URL reachable; this is the guard that
    // actually closes it.
    useWorkspace.mockReturnValue({ hasModule: () => false, isLoading: false })

    renderGuard()

    expect(screen.getByText('home')).toBeInTheDocument()
    expect(screen.queryByText('budgets page')).not.toBeInTheDocument()
  })

  it('asks about the module it was given, not a hard-coded one', () => {
    // Deliberately mismatched: the route is /budgets but the guard is told
    // "invoices". Asking about "budgets" here would pass if the component
    // derived the module from the path or hard-coded it.
    const hasModule = vi.fn().mockReturnValue(true)
    useWorkspace.mockReturnValue({ hasModule, isLoading: false })

    renderWithProviders(
      <Routes>
        <Route path="/" element={<div>home</div>} />
        <Route
          path="/budgets"
          element={
            <ModuleRoute module="invoices">
              <div>budgets page</div>
            </ModuleRoute>
          }
        />
      </Routes>,
      { route: '/budgets' },
    )

    expect(hasModule).toHaveBeenCalledWith('invoices')
    expect(hasModule).not.toHaveBeenCalledWith('budgets')
  })

  it('shows a loading indicator instead of bouncing to home', () => {
    // Redirecting first would make a deep link unusable on a cold load, since
    // enabled_modules has not arrived yet.
    useWorkspace.mockReturnValue({ hasModule: () => false, isLoading: true })

    const { container } = renderGuard()

    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
    expect(screen.queryByText('home')).not.toBeInTheDocument()
    expect(screen.queryByText('budgets page')).not.toBeInTheDocument()
  })
})
