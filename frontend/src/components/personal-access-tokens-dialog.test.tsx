import { screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { PersonalAccessTokensDialog } from '@/components/personal-access-tokens-dialog'
import { personalAccessTokens } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { PersonalAccessToken } from '@/types'

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ current: { id: 'ws-1', name: 'Home' } }),
}))

const existing: PersonalAccessToken = {
  id: 'tok-1',
  name: 'Backup script',
  token_prefix: 'securo_pat_abc123',
  workspace_id: 'ws-1',
  workspace_name: 'Home',
  read_only: true,
  expires_at: '2020-01-01T00:00:00Z',
  last_used_at: null,
  created_at: '2019-01-01T00:00:00Z',
}

afterEach(() => vi.restoreAllMocks())

it('lists tokens with their workspace, read-only and expired state', async () => {
  vi.spyOn(personalAccessTokens, 'list').mockResolvedValue([existing])
  renderWithProviders(<PersonalAccessTokensDialog open onClose={vi.fn()} />)

  const row = (await screen.findByText(existing.name)).closest('div.rounded-lg') as HTMLElement
  expect(within(row).getByText(`${existing.token_prefix}…`)).toBeInTheDocument()
  expect(within(row).getByText(t('accessTokens.expiredBadge'))).toBeInTheDocument()
  expect(within(row).getByText(t('accessTokens.readOnlyBadge'))).toBeInTheDocument()
  expect(within(row).getByText(new RegExp(existing.workspace_name))).toBeInTheDocument()
  expect(screen.getByText(t('accessTokens.workspaceNote', { workspace: 'Home' }))).toBeInTheDocument()
})

it('shows the plaintext once after creating a token', async () => {
  vi.spyOn(personalAccessTokens, 'list').mockResolvedValue([])
  const create = vi.spyOn(personalAccessTokens, 'create').mockResolvedValue({
    ...existing,
    id: 'tok-2',
    name: 'CI',
    read_only: false,
    expires_at: null,
    token: 'securo_pat_secret-value',
  })
  const { user } = renderWithProviders(<PersonalAccessTokensDialog open onClose={vi.fn()} />)

  await screen.findByText(t('accessTokens.empty'))
  await user.type(screen.getByLabelText(t('accessTokens.name')), 'CI')
  await user.click(screen.getByRole('button', { name: t('accessTokens.create') }))

  expect(create).toHaveBeenCalledWith({ name: 'CI', read_only: false, expires_in_days: 90 })
  expect(screen.getByLabelText(t('accessTokens.newToken'))).toHaveValue('securo_pat_secret-value')
  expect(screen.getByText(t('accessTokens.copyNow'))).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: t('accessTokens.done') }))
  expect(screen.queryByDisplayValue('securo_pat_secret-value')).not.toBeInTheDocument()
  expect(screen.getByText('CI')).toBeInTheDocument()
})

it('revokes a token after confirmation', async () => {
  vi.spyOn(personalAccessTokens, 'list').mockResolvedValue([existing])
  const revoke = vi.spyOn(personalAccessTokens, 'revoke').mockResolvedValue()
  const { user } = renderWithProviders(<PersonalAccessTokensDialog open onClose={vi.fn()} />)

  const row = (await screen.findByText(existing.name)).closest('div.rounded-lg') as HTMLElement
  await user.click(within(row).getByRole('button', { name: t('accessTokens.revoke') }))
  expect(revoke).not.toHaveBeenCalled()
  await user.click(within(row).getByRole('button', { name: t('accessTokens.revoke') }))

  expect(revoke).toHaveBeenCalledWith(existing.id)
  expect(screen.queryByText(existing.name)).not.toBeInTheDocument()
})
