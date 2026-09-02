import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { renderWithProviders } from '@/test/utils'

function Example({
  onOpenChange,
  showCloseButton,
}: {
  onOpenChange?: (open: boolean) => void
  showCloseButton?: boolean
} = {}) {
  return (
    <Dialog onOpenChange={onOpenChange}>
      <DialogTrigger>Open</DialogTrigger>
      <DialogContent showCloseButton={showCloseButton}>
        <DialogHeader>
          <DialogTitle>Delete account</DialogTitle>
          <DialogDescription>This cannot be undone.</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <button type="button">Confirm</button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

describe('Dialog', () => {
  it('stays closed until the trigger is pressed', () => {
    renderWithProviders(<Example />)

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens on the trigger and shows its title and description', async () => {
    const { user } = renderWithProviders(<Example />)

    await user.click(screen.getByRole('button', { name: 'Open' }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toBeInTheDocument()
    expect(screen.getByText('Delete account')).toBeInTheDocument()
    expect(screen.getByText('This cannot be undone.')).toBeInTheDocument()
  })

  it('closes on Escape', async () => {
    const { user } = renderWithProviders(<Example />)

    await user.click(screen.getByRole('button', { name: 'Open' }))
    await screen.findByRole('dialog')

    await user.keyboard('{Escape}')

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })

  it('reports open and close through onOpenChange exactly once each', async () => {
    const onOpenChange = vi.fn()
    const { user } = renderWithProviders(<Example onOpenChange={onOpenChange} />)

    await user.click(screen.getByRole('button', { name: 'Open' }))
    await screen.findByRole('dialog')
    expect(onOpenChange).toHaveBeenCalledWith(true)

    await user.keyboard('{Escape}')
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))

    // Count and order, not just "was called with". A dialog that fires open
    // twice would double-submit anything the consumer does on open, and
    // toHaveBeenCalledWith alone cannot tell.
    expect(onOpenChange.mock.calls).toEqual([[true], [false]])
  })

  it('renders a close affordance by default', async () => {
    const { user } = renderWithProviders(<Example />)

    await user.click(screen.getByRole('button', { name: 'Open' }))
    await screen.findByRole('dialog')

    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })

  it('omits the close affordance when the caller opts out', async () => {
    const { user } = renderWithProviders(<Example showCloseButton={false} />)

    await user.click(screen.getByRole('button', { name: 'Open' }))
    await screen.findByRole('dialog')

    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument()
  })
})
