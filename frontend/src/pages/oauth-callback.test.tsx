import { waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import OAuthCallbackPage from './oauth-callback'

const api = vi.hoisted(() => ({
  handleCallback: vi.fn(),
  toastSuccess: vi.fn(),
  toastWarning: vi.fn(),
  toastError: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ connections: { handleCallback: api.handleCallback } }))
vi.mock('@/lib/invalidate-queries', () => ({ invalidateFinancialQueries: vi.fn() }))
vi.mock('sonner', () => ({
  toast: { success: api.toastSuccess, warning: api.toastWarning, error: api.toastError },
}))

beforeEach(() => {
  vi.resetAllMocks()
  sessionStorage.clear()
})

it('warns instead of reporting success when only the first import failed', async () => {
  // The callback answers with the connection kept in error: the bank
  // authorization is not spent, so the message must not read as a failure to
  // connect (issue #1406).
  api.handleCallback.mockResolvedValue({ id: 'connection-1', status: 'error' })
  renderWithProviders(<OAuthCallbackPage />, {
    route: '/oauth/callback?code=synthetic-code&state=synthetic-state',
  })

  await waitFor(() =>
    expect(api.toastWarning).toHaveBeenCalledWith(t('accounts.connectedImportFailed')),
  )
  expect(api.toastSuccess).not.toHaveBeenCalled()
})
