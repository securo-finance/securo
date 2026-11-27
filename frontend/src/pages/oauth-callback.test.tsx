import { waitFor } from '@testing-library/react'
import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import OAuthCallbackPage from './oauth-callback'

const api = vi.hoisted(() => ({
  handleCallback: vi.fn(),
  toastSuccess: vi.fn(),
  toastWarning: vi.fn(),
  toastError: vi.fn(),
}))
vi.mock('@/lib/api', () => ({
  connections: { handleCallback: api.handleCallback },
  quotas: { mine: () => Promise.resolve({ enforced: true, info_url: null, counters: [] }) },
}))
vi.mock('@/lib/invalidate-queries', () => ({ invalidateSyncedRecords: vi.fn() }))
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

function refused(status: number, detail: unknown): AxiosError {
  const config = { headers: new AxiosHeaders() }
  return new AxiosError('refused', undefined, config, undefined, {
    status, statusText: '', headers: {}, config, data: { detail },
  })
}

it('says the connection limit filled when the bank comes back refused (#1972)', async () => {
  api.handleCallback.mockRejectedValue(refused(403, {
    code: 'quota_exceeded', counter: 'max_bank_connections', current: 1, maximum: 1, message: 'x',
  }))
  renderWithProviders(<OAuthCallbackPage />, {
    route: '/oauth/callback?code=synthetic-code&state=synthetic-state',
  })

  await waitFor(() =>
    expect(api.toastError).toHaveBeenCalledWith(t('quota.reached.max_bank_connections', { maximum: 1 }), undefined),
  )
  expect(api.toastError).not.toHaveBeenCalledWith(t('accounts.connectError'))
})

it('shows the message of a coded refusal instead of "[object Object]"', async () => {
  api.handleCallback.mockRejectedValue(refused(400, { code: 'something_else', message: 'The bank said no' }))
  renderWithProviders(<OAuthCallbackPage />, {
    route: '/oauth/callback?code=synthetic-code&state=synthetic-state',
  })

  await waitFor(() => expect(api.toastError).toHaveBeenCalledWith('The bank said no'))
})
