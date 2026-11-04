import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'

import { AgentsRoute } from '@/components/agents-route'
import { renderWithProviders } from '@/test/utils'

const useFeatureFlags = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/use-feature-flags', () => ({ useFeatureFlags }))

function renderGuard() {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<div>home</div>} />
      <Route
        path="/agents"
        element={
          <AgentsRoute>
            <div>agents page</div>
          </AgentsRoute>
        }
      />
    </Routes>,
    { route: '/agents' },
  )
}

describe('AgentsRoute', () => {
  it('renders the page when the server has agents enabled', () => {
    useFeatureFlags.mockReturnValue({ agentsEnabled: true, isLoading: false })

    renderGuard()

    expect(screen.getByText('agents page')).toBeInTheDocument()
  })

  it('sends the user home when AGENTS_ENABLED is off', () => {
    // Agents is opt-in. A deployment that never enabled it must not render a
    // management page whose every API call would 404.
    useFeatureFlags.mockReturnValue({ agentsEnabled: false, isLoading: false })

    renderGuard()

    expect(screen.getByText('home')).toBeInTheDocument()
    expect(screen.queryByText('agents page')).not.toBeInTheDocument()
  })

  it('shows a loading indicator rather than assuming the flag is off', () => {
    useFeatureFlags.mockReturnValue({ agentsEnabled: false, isLoading: true })

    const { container } = renderGuard()

    // Without the spinner assertion this passes on a guard that renders
    // nothing, which is a blank screen rather than a wait.
    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
    expect(screen.queryByText('home')).not.toBeInTheDocument()
    expect(screen.queryByText('agents page')).not.toBeInTheDocument()
  })
})
