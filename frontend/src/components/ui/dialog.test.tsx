import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

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

function Example({ showCloseButton }: { showCloseButton?: boolean } = {}) {
  return (
    <Dialog>
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
