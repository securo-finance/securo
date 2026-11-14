import { fireEvent, screen, waitFor } from '@testing-library/react'
import { Toaster } from 'sonner'
import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  handleCallback: vi.fn(),
  sync: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ connections: { handleCallback: api.handleCallback, sync: api.sync } }))

import { CredentialsConnectDialog } from './credentials-connect-dialog'

const FIELDS = ['id', 'card6Digits', 'password']
const label = (f: string) => t(`accounts.credentialsConnect.fields.${f}`)

function fill() {
  fireEvent.change(screen.getByLabelText(label('id')), { target: { value: '000000018' } })
  fireEvent.change(screen.getByLabelText(label('card6Digits')), { target: { value: '123456' } })
  fireEvent.change(screen.getByLabelText(label('password')), { target: { value: 'secret' } })
}

beforeEach(() => {
  vi.resetAllMocks()
  api.handleCallback.mockResolvedValue({ id: 'conn-1' })
  api.sync.mockResolvedValue({})
})

it('sends the typed login once and closes', async () => {
  const onClose = vi.fn()
  renderWithProviders(
    <>
      <CredentialsConnectDialog open onClose={onClose} provider="isracard" displayName="Isracard" fields={FIELDS} />
      <Toaster />
    </>,
  )
  const connect = screen.getByRole('button', { name: t('accounts.credentialsConnect.connect') })
  expect(connect).toBeDisabled()
  expect(screen.getByLabelText(label('password'))).toHaveAttribute('type', 'password')
  fill()
  fireEvent.click(connect)
  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(api.handleCallback).toHaveBeenCalledWith(
    JSON.stringify({ id: '000000018', card6Digits: '123456', password: 'secret' }),
    'isracard',
    undefined,
    undefined,
    undefined,
  )
  expect(api.sync).not.toHaveBeenCalled()
  expect(await screen.findByText(t('accounts.connected'))).toBeInTheDocument()
})

it('re-syncs after updating the login of an existing connection', async () => {
  const onClose = vi.fn()
  renderWithProviders(
    <>
      <CredentialsConnectDialog
      open onClose={onClose} provider="isracard" displayName="Isracard" fields={FIELDS} reconnectConnectionId="conn-1"
    />
      <Toaster />
    </>,
  )
  fill()
  fireEvent.click(screen.getByRole('button', { name: t('accounts.credentialsConnect.reconnect') }))
  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(api.sync).toHaveBeenCalledWith('conn-1')
})

it('shows the backend message when the login is refused', async () => {
  const { AxiosError, AxiosHeaders } = await import('axios')
  const config = { headers: new AxiosHeaders() }
  api.handleCallback.mockRejectedValue(
    new AxiosError('conflict', undefined, config, undefined, {
      status: 409, statusText: 'Conflict', headers: {}, config,
      data: { detail: { message: 'Isracard rejected the login details.', code: 'credentials_invalid' } },
    }),
  )
  const onClose = vi.fn()
  renderWithProviders(
    <>
      <CredentialsConnectDialog open onClose={onClose} provider="isracard" displayName="Isracard" fields={FIELDS} />
      <Toaster />
    </>,
  )
  fill()
  fireEvent.click(screen.getByRole('button', { name: t('accounts.credentialsConnect.connect') }))
  expect(await screen.findByText('Isracard rejected the login details.')).toBeInTheDocument()
  expect(onClose).not.toHaveBeenCalled()
})
