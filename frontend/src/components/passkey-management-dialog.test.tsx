import { useState } from 'react'
import { act, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { PasskeyManagementDialog } from '@/components/passkey-management-dialog'
import { auth } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Passkey } from '@/types'

vi.mock('@/lib/webauthn', () => ({
  passkeyBlocker: () => null,
  passkeyFailure: () => 'unknown',
  startPasskeyRegistration: vi.fn(),
}))

function DialogHarness() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)}>Manage passkeys</button>
      <PasskeyManagementDialog open={open} onClose={() => setOpen(false)} />
    </>
  )
}

const currentPasskey: Passkey = {
  id: 'current-key',
  name: 'Current key',
  transports: null,
  aaguid: null,
  device_type: null,
  backed_up: null,
  created_at: '2026-01-01T00:00:00Z',
  last_used_at: null,
}

afterEach(() => vi.restoreAllMocks())

it.each(['success', 'failure'])(
  'ignores a previous dialog session list %s after reopening',
  async (outcome) => {
    let resolvePrevious!: (passkeys: Passkey[]) => void
    let rejectPrevious!: (error: Error) => void
    const previousRequest = new Promise<Passkey[]>((resolve, reject) => {
      resolvePrevious = resolve
      rejectPrevious = reject
    })
    const listPasskeys = vi.spyOn(auth, 'listPasskeys')
      .mockReturnValueOnce(previousRequest)
      .mockResolvedValueOnce([currentPasskey])
    const { user } = renderWithProviders(<DialogHarness />)

    await user.click(screen.getByRole('button', { name: 'Manage passkeys' }))
    expect(screen.getByText(t('common.loading'))).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: t('common.close') })[0])
    await user.click(screen.getByRole('button', { name: 'Manage passkeys' }))
    expect(await screen.findByText(currentPasskey.name)).toBeInTheDocument()

    await act(async () => {
      if (outcome === 'success') resolvePrevious([])
      else rejectPrevious(new Error('Previous request failed'))
    })

    expect(screen.getByText(currentPasskey.name)).toBeInTheDocument()
    expect(screen.queryByText(t('auth.passkeyLoadError'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('auth.noPasskeys'))).not.toBeInTheDocument()
    expect(listPasskeys).toHaveBeenCalledTimes(2)
  },
)

it('retries a failed list request in the current dialog session', async () => {
  vi.spyOn(auth, 'listPasskeys')
    .mockRejectedValueOnce(new Error('Request failed'))
    .mockResolvedValueOnce([currentPasskey])
  const { user } = renderWithProviders(<DialogHarness />)

  await user.click(screen.getByRole('button', { name: 'Manage passkeys' }))
  expect(await screen.findByText(t('auth.passkeyLoadError'))).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: t('common.retry') }))

  expect(await screen.findByText(currentPasskey.name)).toBeInTheDocument()
  expect(screen.queryByText(t('auth.passkeyLoadError'))).not.toBeInTheDocument()
})
