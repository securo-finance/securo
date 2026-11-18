import { useState } from 'react'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { RestoreDialog } from '@/components/restore-dialog'
import { backup as backupApi } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'

// The dialog switches workspaces through the provider; the real one would
// fetch the list and reset every cached query on mount.
const workspace = vi.hoisted(() => ({ refresh: vi.fn(), switchWorkspace: vi.fn() }))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => workspace,
}))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  backup: { download: vi.fn(), restore: vi.fn() },
}))

const restored = {
  workspace_id: 'ws-restored',
  workspace_name: 'Restored books',
  entity_counts: { accounts: 2, transactions: 7, categories: 3, rules: 1 },
  // Deliberately unlike any other count below, so `getByText` can find it.
  attachments_restored: 5,
  attachments_missing: 0,
}

function renderDialog() {
  return renderWithProviders(<RestoreDialog open onClose={vi.fn()} />)
}

/**
 * Owns `open` the way the account menu does, so a test can tell a dialog that
 * closed itself from one that is still on screen. It also offers a way back
 * in, which is how the "dismissed mid-restore" case is observed.
 */
function Harness() {
  const [open, setOpen] = useState(true)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        reopen restore
      </button>
      <RestoreDialog open={open} onClose={() => setOpen(false)} />
    </>
  )
}

async function submitBackup(
  user: ReturnType<typeof renderWithProviders>['user'],
  { password }: { password?: string } = {},
) {
  const file = new File(['zip'], 'backup.zip', { type: 'application/zip' })
  await user.upload(screen.getByLabelText(t('backup.restore.fileLabel')), file)
  if (password) {
    await user.type(screen.getByLabelText(t('backup.restore.passwordLabel')), password)
  }
  await user.click(screen.getByRole('button', { name: t('backup.restore.submit') }))
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.switchWorkspace.mockResolvedValue(undefined)
})

describe('RestoreDialog', () => {
  it('sends the file and the password, then reports what came back', async () => {
    ;(backupApi.restore as Mock).mockResolvedValue(restored)
    const { user } = renderDialog()

    await submitBackup(user, { password: 'correct horse battery' })

    await waitFor(() =>
      expect(backupApi.restore).toHaveBeenCalledWith(expect.any(File), 'correct horse battery'),
    )
    expect(
      await screen.findByText(t('backup.restore.success', { name: 'Restored books' })),
    ).toBeInTheDocument()
    // The counts are what the person checks the restore against.
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText(String(restored.attachments_restored))).toBeInTheDocument()
    // This archive carried everything, so there is nothing to warn about.
    expect(screen.queryByText(t('backup.restore.leftOut.title'))).not.toBeInTheDocument()
    // The new workspace must appear in the switcher even if the dialog is
    // closed from here.
    expect(workspace.refresh).toHaveBeenCalled()
  })

  it('says what the backup left out, so a partial restore does not look complete', async () => {
    ;(backupApi.restore as Mock).mockResolvedValue({
      ...restored,
      left_out: { invoices: 3, ui_layouts: 1 },
    })
    const { user } = renderDialog()

    await submitBackup(user)

    expect(await screen.findByText(t('backup.restore.leftOut.title'))).toBeInTheDocument()
    const invoices = screen.getByText(t('backup.restore.leftOut.invoices')).closest('li')
    expect(invoices).toHaveTextContent('3')
    const layouts = screen.getByText(t('backup.restore.leftOut.uiLayouts')).closest('li')
    expect(layouts).toHaveTextContent('1')
    // A family the archive did not report is not invented for the list.
    expect(
      screen.queryByText(t('backup.restore.leftOut.connections')),
    ).not.toBeInTheDocument()
  })

  it('opens no password field it does not need: an unencrypted archive goes as is', async () => {
    ;(backupApi.restore as Mock).mockResolvedValue(restored)
    const { user } = renderDialog()

    await submitBackup(user)

    await waitFor(() =>
      expect(backupApi.restore).toHaveBeenCalledWith(expect.any(File), undefined),
    )
  })

  it('switches to the restored workspace and closes the dialog', async () => {
    ;(backupApi.restore as Mock).mockResolvedValue(restored)
    const { user } = renderWithProviders(<Harness />)
    await submitBackup(user)

    await user.click(
      await screen.findByRole('button', { name: t('backup.restore.openWorkspace') }),
    )

    expect(workspace.switchWorkspace).toHaveBeenCalledWith('ws-restored')
    // The result screen must not stay behind the workspace it just opened:
    // without `handleClose()` in `handleOpenWorkspace` this stays on screen.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('does not bring back a restore the user dismissed while it was running', async () => {
    let finish: (value: typeof restored) => void = () => {}
    ;(backupApi.restore as Mock).mockReturnValue(
      new Promise<typeof restored>((resolve) => {
        finish = resolve
      }),
    )
    const { user } = renderWithProviders(<Harness />)
    await submitBackup(user)

    // The user gives up waiting and closes the dialog.
    await user.click(screen.getByRole('button', { name: t('common.cancel') }))
    finish(restored)
    // The workspace list is still refreshed: the restore did happen.
    await waitFor(() => expect(workspace.refresh).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: 'reopen restore' }))
    // Reopening must show the upload form, not the success screen of a
    // session the user already closed.
    expect(screen.getByRole('button', { name: t('backup.restore.submit') })).toBeInTheDocument()
    expect(
      screen.queryByText(t('backup.restore.success', { name: 'Restored books' })),
    ).not.toBeInTheDocument()
  })

  it('shows the reason the server gave for refusing the file', async () => {
    ;(backupApi.restore as Mock).mockRejectedValue({
      response: { data: { detail: 'This backup is in format 1.2; this version restores 1.1.' } },
    })
    const { user } = renderDialog()

    await submitBackup(user)

    expect(await screen.findByText(/format 1\.2/)).toBeInTheDocument()
    expect(workspace.refresh).not.toHaveBeenCalled()
  })

  it('will not send without a file', () => {
    renderDialog()
    expect(screen.getByRole('button', { name: t('backup.restore.submit') })).toBeDisabled()
  })
})
