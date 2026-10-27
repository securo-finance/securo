import { fireEvent, screen, waitFor } from '@testing-library/react'
import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  handleCallback: vi.fn(),
  waitForNewConnection: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ connections: { list: api.list, handleCallback: api.handleCallback } }))
vi.mock('@/lib/await-new-connection', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/await-new-connection')>()),
  waitForNewConnection: api.waitForNewConnection,
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { id: 'me' } }) }))
vi.mock('sonner', () => ({ toast: { success: api.toastSuccess, error: api.toastError } }))

import { TokenConnectDialog } from './token-connect-dialog'

function gatewayTimeout(): AxiosError {
  const config = { headers: new AxiosHeaders() }
  return new AxiosError('timeout', undefined, config, undefined, {
    status: 504, statusText: 'Gateway Timeout', headers: {}, config, data: '<html>504</html>',
  })
}

function submit(onClose = vi.fn()) {
  renderWithProviders(<TokenConnectDialog open onClose={onClose} provider="simplefin" />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'setup-token' } })
  fireEvent.click(screen.getByRole('button', { name: t('accounts.tokenConnect.connect') }))
  return onClose
}

beforeEach(() => {
  vi.resetAllMocks()
  api.list.mockResolvedValue([{ id: 'existing', provider: 'simplefin' }])
})

it('reports success when a slow import outlasts the proxy and then lands (#1148)', async () => {
  api.handleCallback.mockRejectedValue(gatewayTimeout())
  api.waitForNewConnection.mockResolvedValue({ id: 'new', provider: 'simplefin' })

  const onClose = submit()

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(api.waitForNewConnection).toHaveBeenCalledWith('simplefin', new Set(['existing']), 'me')
  expect(api.toastSuccess).toHaveBeenCalledWith(t('accounts.connected'))
  expect(api.toastError).not.toHaveBeenCalled()
})

it('still reports the failure when no connection appears', async () => {
  api.handleCallback.mockRejectedValue(gatewayTimeout())
  api.waitForNewConnection.mockResolvedValue(null)

  const onClose = submit()

  await waitFor(() => expect(api.toastError).toHaveBeenCalledWith(t('accounts.connectError')))
  expect(onClose).not.toHaveBeenCalled()
})

it('does not wait on an error the backend itself returned', async () => {
  const config = { headers: new AxiosHeaders() }
  api.handleCallback.mockRejectedValue(new AxiosError('bad', undefined, config, undefined, {
    status: 400, statusText: '', headers: {}, config, data: { detail: 'Invalid token' },
  }))

  submit()

  await waitFor(() => expect(api.toastError).toHaveBeenCalledWith('Invalid token'))
  expect(api.waitForNewConnection).not.toHaveBeenCalled()
})
