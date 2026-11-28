import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '@/test/utils'
import { clearPluginOAuthState, peekPluginOAuthState, rememberPluginOAuthState } from '@/lib/plugin-oauth'
import PluginOAuthCallbackPage from './plugin-oauth-callback'

const api = vi.hoisted(() => ({
  plugins: {
    oauthCallback: vi.fn(),
  },
}))

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))

describe('PluginOAuthCallbackPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearPluginOAuthState()
  })

  it('completes a hosted sign-in callback with no code using sessionStorage state and query params', async () => {
    rememberPluginOAuthState('state-hosted-1')
    api.plugins.oauthCallback.mockResolvedValue({
      id: 'c1',
      plugin_id: 'your-name/plaid',
      integration: 'plaid',
      name: 'Plaid',
      status: 'pending',
    })

    renderWithProviders(
      <Routes>
        <Route path="/plugins/oauth/callback" element={<PluginOAuthCallbackPage />} />
        <Route path="/plugins/:owner/:name" element={<div>plugin-detail</div>} />
      </Routes>,
      { route: '/plugins/oauth/callback?status=success&link_session_id=ls-1' },
    )

    await waitFor(() =>
      expect(api.plugins.oauthCallback).toHaveBeenCalledWith(
        null,
        'state-hosted-1',
        { status: 'success', link_session_id: 'ls-1' },
      ),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('plugin-detail')).toBeInTheDocument())
    expect(peekPluginOAuthState()).toBeNull()
  })

  it('completes a traditional OAuth callback with code and state in the URL', async () => {
    api.plugins.oauthCallback.mockResolvedValue({
      id: 'c2',
      plugin_id: 'acme/ledger',
      integration: 'signin',
      name: 'Ledger',
      status: 'pending',
    })

    renderWithProviders(
      <Routes>
        <Route path="/plugins/oauth/callback" element={<PluginOAuthCallbackPage />} />
        <Route path="/plugins/:owner/:name" element={<div>plugin-detail</div>} />
      </Routes>,
      { route: '/plugins/oauth/callback?code=auth-code-9&state=state-url-2' },
    )

    await waitFor(() =>
      expect(api.plugins.oauthCallback).toHaveBeenCalledWith(
        'auth-code-9',
        'state-url-2',
        { code: 'auth-code-9' },
      ),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('plugin-detail')).toBeInTheDocument())
  })

  it('fails cleanly when state is missing from both URL and sessionStorage', async () => {
    renderWithProviders(
      <Routes>
        <Route path="/plugins/oauth/callback" element={<PluginOAuthCallbackPage />} />
        <Route path="/plugins" element={<div>plugins-list</div>} />
      </Routes>,
      { route: '/plugins/oauth/callback?status=success' },
    )

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('plugins-list')).toBeInTheDocument())
    expect(api.plugins.oauthCallback).not.toHaveBeenCalled()
  })
})
