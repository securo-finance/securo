import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { AxiosError, AxiosHeaders } from 'axios'
import { Route, Routes } from 'react-router-dom'

import OAuthCallbackPage from '@/pages/oauth-callback'
import { renderWithProviders, t, i18n } from '@/test/utils'

const api = vi.hoisted(() => ({ handleCallback: vi.fn(), toastError: vi.fn() }))
vi.mock('@/lib/api', () => ({ connections: { handleCallback: api.handleCallback } }))
vi.mock('sonner', () => ({ toast: { error: api.toastError } }))

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
})

afterEach(async () => { await i18n.changeLanguage('en') })

it.each(['en', 'pt-BR'])('localizes the bounded sync conflict and returns to accounts to restart reconnect (%s)', async (language) => {
  await i18n.changeLanguage(language)
  const message = 'Connection sync in progress. Please try again shortly.'
  const config = { headers: new AxiosHeaders() }
  api.handleCallback.mockRejectedValue(new AxiosError('conflict', undefined, config, undefined, {
    status: 409, statusText: 'Conflict', headers: {}, config,
    data: { detail: { code: 'sync_in_progress', message } },
  }))
  renderWithProviders(
    <Routes>
      <Route path="/oauth/callback" element={<OAuthCallbackPage />} />
      <Route path="/accounts" element={<div>Back to accounts</div>} />
    </Routes>,
    { route: '/oauth/callback?code=test-code&state=test-state' },
  )

  await waitFor(() => expect(api.toastError).toHaveBeenCalledWith(t('accounts.syncInProgress')))
  expect(await screen.findByText('Back to accounts')).toBeInTheDocument()
  expect(api.handleCallback).toHaveBeenCalledExactlyOnceWith('test-code', '', 'test-state')
  expect(sessionStorage.getItem('oauth-submitted:test-code:test-state')).toBeNull()
})
