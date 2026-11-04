import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { renderWithProviders, t } from '@/test/utils'

const baseProps = {
  open: true,
  title: 'Delete category',
  description: 'Groceries will be removed from 12 transactions.',
  isPending: false,
  onClose: vi.fn(),
  onConfirm: vi.fn(),
}

describe('DeleteConfirmationDialog', () => {
  it('renders nothing while closed', () => {
    renderWithProviders(
      <DeleteConfirmationDialog {...baseProps} open={false} />,
    )

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the caller-supplied title and description', () => {
    renderWithProviders(<DeleteConfirmationDialog {...baseProps} />)

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Delete category')).toBeInTheDocument()
    expect(
      screen.getByText('Groceries will be removed from 12 transactions.'),
    ).toBeInTheDocument()
  })

  it('labels its actions with the shipped copy', () => {
    // Deliberately the literal English, not t('common.cancel'). Looking the
    // label up through the same key the component uses makes the assertion
    // circular: it would still pass if the key resolved to the raw key
    // string, which is exactly how a missing translation renders.
    renderWithProviders(<DeleteConfirmationDialog {...baseProps} />)

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Delete/ })).toBeInTheDocument()

    // And the keys really are the ones the component reads.
    expect(t('common.cancel')).toBe('Cancel')
    expect(t('common.delete')).toBe('Delete')
  })

  it('confirms through onConfirm', async () => {
    const onConfirm = vi.fn()
    const { user } = renderWithProviders(
      <DeleteConfirmationDialog {...baseProps} onConfirm={onConfirm} />,
    )

    await user.click(
      screen.getByRole('button', { name: new RegExp(t('common.delete')) }),
    )

    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('cancels through onClose when no explicit onCancel is given', async () => {
    const onClose = vi.fn()
    const { user } = renderWithProviders(
      <DeleteConfirmationDialog {...baseProps} onClose={onClose} />,
    )

    await user.click(screen.getByRole('button', { name: t('common.cancel') }))

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('prefers an explicit onCancel over onClose', async () => {
    // #643: cancelling a nested confirmation has to return to the parent
    // dialog, which is a different action from dismissing the whole stack.
    const onClose = vi.fn()
    const onCancel = vi.fn()
    const { user } = renderWithProviders(
      <DeleteConfirmationDialog
        {...baseProps}
        onClose={onClose}
        onCancel={onCancel}
      />,
    )

    await user.click(screen.getByRole('button', { name: t('common.cancel') }))

    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes on Escape while idle', async () => {
    const onClose = vi.fn()
    const { user } = renderWithProviders(
      <DeleteConfirmationDialog {...baseProps} onClose={onClose} />,
    )

    await user.keyboard('{Escape}')

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('disables both actions while the delete is in flight', () => {
    // Without this a double click fires two DELETEs, and the second one
    // 404s on a row the first already removed.
    renderWithProviders(<DeleteConfirmationDialog {...baseProps} isPending />)

    expect(screen.getByRole('button', { name: t('common.cancel') })).toBeDisabled()
    expect(
      screen.getByRole('button', { name: new RegExp(t('common.delete')) }),
    ).toBeDisabled()
  })

  it('refuses to close on Escape while the delete is in flight', async () => {
    // The dialog is the only progress indicator the user has; dismissing it
    // mid-request leaves them unsure whether the row was deleted.
    const onClose = vi.fn()
    const { user } = renderWithProviders(
      <DeleteConfirmationDialog {...baseProps} isPending onClose={onClose} />,
    )

    await user.keyboard('{Escape}')

    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('hides the corner close affordance while pending', () => {
    renderWithProviders(<DeleteConfirmationDialog {...baseProps} isPending />)

    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument()
  })

  it('stays open after confirming, so the caller controls dismissal on error', async () => {
    // #643 again: on a failed delete the dialog must remain so the user can
    // read the toast and back out deliberately.
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    const { user } = renderWithProviders(
      <DeleteConfirmationDialog
        {...baseProps}
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    )

    await user.click(
      screen.getByRole('button', { name: new RegExp(t('common.delete')) }),
    )

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })
})
