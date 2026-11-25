import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { EmailChangeDialog } from '@/components/email-change-dialog'
import { renderWithProviders } from '@/test/utils'
import type { User } from '@/types'

// The account the dialog is shown for lives here, and updateUser writes to it:
// what the owner sees after an action is the account as the app now holds it,
// not just the call the component made.
const updateUser = vi.hoisted(() =>
  vi.fn((updated: User) => {
    state.user = updated
  }),
)
const auth = vi.hoisted(() => ({
  confirmEmailChange: vi.fn(),
  declineEmailChange: vi.fn(),
  me: vi.fn(),
}))
const state = vi.hoisted(() => ({ user: null as User | null }))
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}))

/** The refusal the API answers when the address on screen is not the one waiting. */
function badRequest() {
  return Object.assign(new Error('bad request'), {
    isAxiosError: true,
    response: { status: 400 },
  })
}

vi.mock('sonner', () => ({ toast }))
vi.mock('@/lib/api', () => ({ auth }))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: state.user, updateUser }),
}))

function account(overrides: Partial<User> = {}): User {
  return {
    id: 'user-1',
    email: 'owner@example.com',
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_2fa_enabled: false,
    preferences: {},
    pending_email: 'asked@example.com',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  state.user = account()
})

describe('EmailChangeDialog', () => {
  it('asks the person whose account it is before the address moves', async () => {
    renderWithProviders(<EmailChangeDialog />)

    expect(await screen.findByText('Change of email address')).toBeInTheDocument()
    expect(
      screen.getByText(/asked@example\.com/),
    ).toBeInTheDocument()
  })

  it('accepting moves the account to the new address', async () => {
    auth.confirmEmailChange.mockResolvedValue(
      account({ email: 'asked@example.com', pending_email: null }),
    )

    const { user } = renderWithProviders(<EmailChangeDialog />)

    await user.click(screen.getByRole('button', { name: 'Accept the new address' }))

    // The address that was on screen goes back with the acceptance, so what
    // moves is the one the owner read (ADR 0042).
    await waitFor(() =>
      expect(auth.confirmEmailChange).toHaveBeenCalledWith('asked@example.com'),
    )
    expect(updateUser).toHaveBeenCalledWith(
      expect.objectContaining({ email: 'asked@example.com', pending_email: null }),
    )
    // And the question is gone: the account is on its new address.
    await waitFor(() =>
      expect(screen.queryByText('Change of email address')).not.toBeInTheDocument(),
    )
  })

  it('declining keeps the current address', async () => {
    auth.declineEmailChange.mockResolvedValue(undefined)

    const { user } = renderWithProviders(<EmailChangeDialog />)

    await user.click(screen.getByRole('button', { name: 'Keep my current address' }))

    await waitFor(() => expect(auth.declineEmailChange).toHaveBeenCalled())
    expect(updateUser).toHaveBeenCalledWith(
      expect.objectContaining({ email: 'owner@example.com', pending_email: null }),
    )
    await waitFor(() =>
      expect(screen.queryByText('Change of email address')).not.toBeInTheDocument(),
    )
  })

  it('says so when the address was taken in the meantime', async () => {
    auth.confirmEmailChange.mockRejectedValue(new Error('taken'))

    const { user } = renderWithProviders(<EmailChangeDialog />)

    await user.click(screen.getByRole('button', { name: 'Accept the new address' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    // Nothing moved: the account stays as it was and the request is still open.
    expect(updateUser).not.toHaveBeenCalled()
    expect(screen.getByText('Change of email address')).toBeInTheDocument()
  })

  it('reads the new request when the one on screen was replaced', async () => {
    // An administrator replaced the request while the dialog was open, so the
    // address on screen is no longer the one waiting and the acceptance is
    // refused. The dialog must show the request that is really pending, not
    // repeat the same refusal until the page reloads (#569).
    auth.confirmEmailChange.mockRejectedValue(badRequest())
    auth.me.mockResolvedValue(
      account({ pending_email: 'later@example.com' }),
    )

    const { user } = renderWithProviders(<EmailChangeDialog />)

    await user.click(screen.getByRole('button', { name: 'Accept the new address' }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'The administrator sent a new request. Check the new address before accepting',
      ),
    )
    expect(await screen.findByText(/later@example\.com/)).toBeInTheDocument()
  })

  it('closes when the request was withdrawn in the meantime', async () => {
    auth.confirmEmailChange.mockRejectedValue(badRequest())
    auth.me.mockResolvedValue(account({ pending_email: null }))

    const { user } = renderWithProviders(<EmailChangeDialog />)

    await user.click(screen.getByRole('button', { name: 'Accept the new address' }))

    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(
        'The request was withdrawn. Your account keeps its current address',
      ),
    )
    expect(updateUser).toHaveBeenCalledWith(
      expect.objectContaining({ pending_email: null }),
    )
    await waitFor(() =>
      expect(screen.queryByText('Change of email address')).not.toBeInTheDocument(),
    )
  })

  it('reopens when an administrator replaces the request that was put away', async () => {
    const { user, rerender } = renderWithProviders(<EmailChangeDialog />)

    // The owner closes it without deciding; the request is still waiting.
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() =>
      expect(screen.queryByText('Change of email address')).not.toBeInTheDocument(),
    )

    // An administrator replaces it: the new address has not been read yet.
    state.user = account({ pending_email: 'later@example.com' })
    rerender(<EmailChangeDialog />)

    expect(await screen.findByText('Change of email address')).toBeInTheDocument()
    expect(screen.getByText(/later@example\.com/)).toBeInTheDocument()
  })

  it('shows nothing when no change was asked for', () => {
    state.user = account({ pending_email: null })

    renderWithProviders(<EmailChangeDialog />)

    expect(screen.queryByText('Change of email address')).not.toBeInTheDocument()
  })
})
