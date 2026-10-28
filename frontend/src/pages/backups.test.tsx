import { act, screen, waitFor } from '@testing-library/react'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import BackupPage from '@/pages/backups'
import { renderWithProviders, t } from '@/test/utils'
import type { BackupConfig, BackupItem, BackupPreview } from '@/lib/api'

const api = vi.hoisted(() => ({
  getConfig: vi.fn(),
  updateConfig: vi.fn(),
  list: vi.fn(),
  run: vi.fn(),
  download: vi.fn(),
  downloadStored: vi.fn(),
  previewUpload: vi.fn(),
  restoreUpload: vi.fn(),
  restoreStored: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  backup: api,
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({
    current: { id: 'workspace-1' },
    refresh: vi.fn().mockResolvedValue(undefined),
  }),
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-US',
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const config: BackupConfig = {
  scheduled_enabled: false,
  storage_shared: true,
  schedule: 'daily',
  content: 'both',
  retention_count: 10,
  retention_days: null,
  last_run_at: null,
  next_run_at: null,
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function preview(name: string): BackupPreview {
  return {
    valid: true,
    format_version: '1',
    export_date: '2026-10-24T12:00:00Z',
    workspace_id: 'workspace-1',
    workspace_name: name,
    content: 'both',
    entity_counts: { accounts: 1 },
    warnings: [],
  }
}

describe('BackupPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getConfig.mockResolvedValue(config)
    api.list.mockResolvedValue([])
  })

  it('keeps Save disabled until the fetched configuration hydrates the form', async () => {
    const configRequest = deferred<BackupConfig>()
    api.getConfig.mockReturnValue(configRequest.promise)
    const { queryClient } = renderWithProviders(<BackupPage />)

    const save = screen.getByRole('button', { name: t('common.save') })
    const scheduleSwitch = screen.getByRole('switch', { name: t('backup.scheduleTitle') })
    expect(save).toBeDisabled()
    expect(scheduleSwitch).toBeDisabled()
    expect(queryClient.getQueryState(['backup-config', 'workspace-1'])).toBeDefined()
    expect(queryClient.getQueryState(['backups', 'workspace-1'])).toBeDefined()

    await act(async () => configRequest.resolve(config))
    await waitFor(() => expect(save).toBeEnabled())
    expect(scheduleSwitch).toBeEnabled()
  })

  it('shows a retryable error and disables scheduling when configuration loading fails', async () => {
    api.getConfig
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce(config)
    const { user } = renderWithProviders(<BackupPage />)

    expect(await screen.findByText(t('backup.configLoadError'))).toBeInTheDocument()
    const scheduleSwitch = screen.getByRole('switch', { name: t('backup.scheduleTitle') })
    expect(scheduleSwitch).toBeDisabled()
    await user.click(screen.getByRole('button', { name: t('common.retry') }))
    await waitFor(() => expect(screen.queryByText(t('backup.configLoadError'))).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: t('common.save') })).toBeEnabled())
    expect(scheduleSwitch).toBeEnabled()
  })

  it('keeps unsaved edits when an identical config refetch completes', async () => {
    const { container, queryClient, user } = renderWithProviders(<BackupPage />)
    const save = await screen.findByRole('button', { name: t('common.save') })
    await waitFor(() => expect(save).toBeEnabled())
    const retentionCount = container.querySelector('input[type="number"]') as HTMLInputElement
    await user.clear(retentionCount)
    await user.type(retentionCount, '7')

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['backup-config'] })
    })

    await waitFor(() => expect(api.getConfig).toHaveBeenCalledTimes(2))
    expect(retentionCount).toHaveValue(7)
  })

  it('ignores an older upload preview that resolves after the current file', async () => {
    const firstRequest = deferred<BackupPreview>()
    const secondRequest = deferred<BackupPreview>()
    api.previewUpload
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(secondRequest.promise)
    const { container, user } = renderWithProviders(<BackupPage />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement

    await user.upload(input, new File(['first'], 'first.zip', { type: 'application/zip' }))
    await user.upload(input, new File(['second'], 'second.zip', { type: 'application/zip' }))
    const restoreButton = screen.getByRole('button', { name: t('backup.restore') })
    expect(restoreButton).toBeDisabled()

    await act(async () => secondRequest.resolve(preview('Workspace B')))
    expect(await screen.findByText('Workspace B')).toBeInTheDocument()
    expect(restoreButton).toBeEnabled()

    await act(async () => firstRequest.resolve(preview('Workspace A')))
    expect(screen.getByText('Workspace B')).toBeInTheDocument()
    expect(screen.queryByText('Workspace A')).not.toBeInTheDocument()
  })

  it('keeps restore disabled when upload preview fails', async () => {
    api.previewUpload.mockRejectedValue(new Error('invalid backup'))
    const { container, user } = renderWithProviders(<BackupPage />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement

    await user.upload(input, new File(['invalid'], 'invalid.zip', { type: 'application/zip' }))

    const restoreButton = screen.getByRole('button', { name: t('backup.restore') })
    await waitFor(() => expect(api.previewUpload).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(restoreButton).toBeDisabled()
  })

  it('shows a retryable error instead of an empty list when loading stored backups fails', async () => {
    api.list.mockRejectedValue(new Error('network unavailable'))
    const { user } = renderWithProviders(<BackupPage />)

    expect(await screen.findByText(t('backup.listError'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('common.retry') }))
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2))
  })

  it('shows a clean total for entity types omitted from the compact breakdown', async () => {
    const item: BackupItem = {
      id: 'backup-1',
      filename: 'backup.zip',
      size_bytes: 128,
      created_at: '2026-10-24T12:00:00Z',
      workspace_id: 'workspace-1',
      workspace_name: 'Workspace',
      content: 'both',
      entity_counts: { products: 2 },
    }
    api.list.mockResolvedValue([item])
    renderWithProviders(<BackupPage />)

    const summary = await screen.findByText(/Total: 2/)
    expect(summary).not.toHaveTextContent(t('backup.counts.empty'))
  })
})
