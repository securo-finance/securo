import { screen, waitFor } from '@testing-library/react'
import { AxiosError, AxiosHeaders } from 'axios'
import { Toaster } from 'sonner'
import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  getConnectToken: vi.fn(),
  getQrSession: vi.fn(),
  handleCallback: vi.fn(),
  sync: vi.fn(),
}))
vi.mock('@/lib/api', () => ({
  connections: {
    getConnectToken: api.getConnectToken,
    getQrSession: api.getQrSession,
    handleCallback: api.handleCallback,
    sync: api.sync,
  },
}))

import { QrConnectDialog } from './qr-connect-dialog'

const SESSION = '11111111-2222-4333-8444-555555555555'

beforeEach(() => {
  vi.resetAllMocks()
  api.getConnectToken.mockResolvedValue(SESSION)
  api.handleCallback.mockResolvedValue({ id: 'conn-1' })
  api.sync.mockResolvedValue({})
})

it('shows the QR, then imports once the scan is done', async () => {
  api.getQrSession
    .mockResolvedValueOnce({ status: 'waiting_for_scan', qr: 'iVBORw0KGgo=', error: null })
    .mockResolvedValue({ status: 'ready', qr: null, error: null })
  const onClose = vi.fn()
  renderWithProviders(
    <>
      <QrConnectDialog open onClose={onClose} provider="leumi" />
      <Toaster />
    </>,
  )

  const img = await screen.findByRole('img', { name: t('accounts.qrConnect.qrAlt') })
  expect(img.getAttribute('src')).toBe('data:image/png;base64,iVBORw0KGgo=')
  expect(screen.getByText(t('accounts.qrConnect.waiting'))).toBeTruthy()

  await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 5000 })
  expect(api.getConnectToken).toHaveBeenCalledWith('leumi')
  expect(api.handleCallback).toHaveBeenCalledWith(SESSION, 'leumi', undefined, undefined, undefined)
  expect(api.sync).not.toHaveBeenCalled()
  expect(await screen.findByText(t('accounts.connected'))).toBeInTheDocument()
})

it('re-syncs the existing connection after a reconnect scan', async () => {
  api.getQrSession.mockResolvedValue({ status: 'ready', qr: null, error: null })
  const onClose = vi.fn()
  renderWithProviders(
    <>
      <QrConnectDialog open onClose={onClose} provider="leumi" reconnectConnectionId="conn-1" />
      <Toaster />
    </>,
  )
  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(api.handleCallback).toHaveBeenCalledWith(SESSION, 'leumi', undefined, undefined, 'conn-1')
  expect(api.sync).toHaveBeenCalledWith('conn-1')
  expect(await screen.findByText(t('accounts.reconnected'))).toBeInTheDocument()
})

it('offers to start again when the code expires', async () => {
  const config = { headers: new AxiosHeaders() }
  api.getQrSession.mockRejectedValue(
    new AxiosError('gone', undefined, config, undefined, {
      status: 410, statusText: 'Gone', headers: {}, config, data: { detail: 'expired' },
    }),
  )
  renderWithProviders(
    <>
      <QrConnectDialog open onClose={vi.fn()} provider="leumi" />
      <Toaster />
    </>,
  )
  expect(await screen.findByText(t('accounts.qrConnect.expired'))).toBeTruthy()
  expect(screen.getByRole('button', { name: t('accounts.qrConnect.retry') })).toBeTruthy()
  expect(api.handleCallback).not.toHaveBeenCalled()
})

it('shows the bank error and does not import', async () => {
  api.getQrSession.mockResolvedValue({ status: 'error', qr: null, error: 'TIMEOUT: QR was not scanned in time' })
  renderWithProviders(
    <>
      <QrConnectDialog open onClose={vi.fn()} provider="leumi" />
      <Toaster />
    </>,
  )
  expect(await screen.findByRole('alert')).toHaveTextContent('QR was not scanned in time')
  expect(api.handleCallback).not.toHaveBeenCalled()
})

it('keeps one session when the parent re-renders with a new onClose', async () => {
  api.getQrSession.mockResolvedValue({ status: 'waiting_for_scan', qr: 'iVBORw0KGgo=', error: null })
  const { rerender } = renderWithProviders(<QrConnectDialog open onClose={() => {}} provider="leumi" />)
  await screen.findByRole('img', { name: t('accounts.qrConnect.qrAlt') })
  rerender(<QrConnectDialog open onClose={() => {}} provider="leumi" />)
  rerender(<QrConnectDialog open onClose={() => {}} provider="leumi" />)
  expect(api.getConnectToken).toHaveBeenCalledTimes(1)
})
