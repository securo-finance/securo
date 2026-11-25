import { screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { AccessTokensDialog } from '@/components/access-tokens-dialog'
import { accessTokens, workspaces } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { AccessToken, WorkspaceMember } from '@/types'

const workspace = vi.hoisted(() => ({ canWrite: true }))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ current: { id: 'ws-1', name: 'Home' }, canWrite: workspace.canWrite }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: 'user-1' } }),
}))

const SCOPES = ['accounts:read', 'accounts:write', 'transactions:read', 'transactions:write', 'reports:read']

const existing: AccessToken = {
  id: 'tok-1',
  user_id: 'user-1',
  name: 'Backup script',
  token_prefix: 'sct_abc123',
  scopes: ['accounts:read', 'reports:read'],
  created_at: '2019-01-01T00:00:00Z',
  expires_at: '2020-01-01T00:00:00Z',
  last_used_at: null,
  revoked_at: null,
  is_active: false,
}

const rowOf = (name: string) =>
  screen.getByText(name).closest('[data-testid="access-token-row"]') as HTMLElement

const scopeBox = (resource: string, level: 'scopeRead' | 'scopeWrite') =>
  screen.getByRole('checkbox', {
    name: `${t(`plugins.code.resource.${resource}`)}: ${t(`accessTokens.${level}`)}`,
  })

beforeEach(() => {
  workspace.canWrite = true
  vi.spyOn(accessTokens, 'scopes').mockResolvedValue(SCOPES)
})

afterEach(() => vi.restoreAllMocks())

it('lists tokens with their scopes, read-only and expired state', async () => {
  vi.spyOn(accessTokens, 'list').mockResolvedValue([existing])
  renderWithProviders(<AccessTokensDialog open onClose={vi.fn()} />)

  await screen.findByText(existing.name)
  const row = rowOf(existing.name)
  expect(within(row).getByText(`${existing.token_prefix}…`)).toBeInTheDocument()
  expect(
    within(row).getByText(t('plugins.code.read', { what: 'accounts, reports' })),
  ).toBeInTheDocument()
  // The form only appears after asking for a new token.
  expect(screen.queryByLabelText(t('accessTokens.name'))).not.toBeInTheDocument()
  expect(within(row).getByText(t('accessTokens.expiredBadge'))).toBeInTheDocument()
  expect(within(row).getByText(t('accessTokens.readOnlyBadge'))).toBeInTheDocument()
})

it("names the member who created another member's token", async () => {
  vi.spyOn(accessTokens, 'list').mockResolvedValue([
    { ...existing, user_id: 'user-2', scopes: ['accounts:write'] },
  ])
  vi.spyOn(workspaces, 'listMembers').mockResolvedValue([
    { user_id: 'user-2', email: 'ana@example.com', display_name: 'Ana' } as WorkspaceMember,
  ])
  renderWithProviders(<AccessTokensDialog open onClose={vi.fn()} />)

  await screen.findByText(existing.name)
  const row = rowOf(existing.name)
  expect(within(row).getByText(t('accessTokens.createdBy', { member: 'Ana' }))).toBeInTheDocument()
  expect(within(row).queryByText(t('accessTokens.readOnlyBadge'))).not.toBeInTheDocument()
})

it('creates a token with the chosen scopes and shows the plaintext once', async () => {
  vi.spyOn(accessTokens, 'list').mockResolvedValue([])
  const create = vi.spyOn(accessTokens, 'create').mockResolvedValue({
    ...existing,
    id: 'tok-2',
    name: 'CI',
    scopes: ['accounts:write', 'transactions:read'],
    expires_at: null,
    is_active: true,
    token: 'sct_secret-value',
  })
  const { user } = renderWithProviders(<AccessTokensDialog open onClose={vi.fn()} />)

  await screen.findByText(t('accessTokens.empty'))
  await user.click(screen.getByRole('button', { name: t('accessTokens.newToken') }))
  const submit = screen.getByRole('button', { name: t('accessTokens.create') })
  await user.type(screen.getByLabelText(t('accessTokens.name')), 'CI')
  expect(submit).toBeDisabled()

  await user.click(scopeBox('accounts', 'scopeWrite'))
  expect(scopeBox('accounts', 'scopeRead')).toBeChecked()
  expect(scopeBox('accounts', 'scopeRead')).toBeDisabled()
  await user.click(scopeBox('transactions', 'scopeRead'))
  await user.click(submit)

  expect(create).toHaveBeenCalledWith({
    name: 'CI',
    scopes: ['accounts:write', 'transactions:read'],
    expires_in_days: 90,
  })
  expect(screen.getByLabelText(t('accessTokens.newToken'))).toHaveValue('sct_secret-value')
  expect(screen.getByText(t('accessTokens.copyNow'))).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: t('accessTokens.done') }))
  expect(screen.queryByDisplayValue('sct_secret-value')).not.toBeInTheDocument()
  expect(screen.getByText('CI')).toBeInTheDocument()
})

it('selects every scope from the column headers', async () => {
  vi.spyOn(accessTokens, 'list').mockResolvedValue([])
  const create = vi.spyOn(accessTokens, 'create').mockRejectedValue(new Error('stop'))
  const { user } = renderWithProviders(<AccessTokensDialog open onClose={vi.fn()} />)

  await screen.findByText(t('accessTokens.empty'))
  await user.click(screen.getByRole('button', { name: t('accessTokens.newToken') }))
  await user.type(screen.getByLabelText(t('accessTokens.name')), 'All')
  await user.click(screen.getByRole('checkbox', { name: t('accessTokens.scopeWrite') }))
  await user.click(screen.getByRole('checkbox', { name: t('accessTokens.scopeRead') }))
  await user.click(screen.getByRole('button', { name: t('accessTokens.create') }))

  // Reports has no write scope, so it stays at read.
  expect(create).toHaveBeenCalledWith({
    name: 'All',
    scopes: ['accounts:write', 'transactions:write', 'reports:read'],
    expires_in_days: 90,
  })
})

it('offers a read-only member only read scopes', async () => {
  workspace.canWrite = false
  vi.spyOn(accessTokens, 'list').mockResolvedValue([])
  const { user } = renderWithProviders(<AccessTokensDialog open onClose={vi.fn()} />)

  await screen.findByText(t('accessTokens.empty'))
  await user.click(screen.getByRole('button', { name: t('accessTokens.newToken') }))
  expect(scopeBox('accounts', 'scopeWrite')).toBeDisabled()
  expect(scopeBox('accounts', 'scopeRead')).toBeEnabled()
  expect(screen.getByText(t('accessTokens.viewerNote'))).toBeInTheDocument()
  expect(screen.getByText(t('accessTokens.workspaceNote', { workspace: 'Home' }))).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: t('common.back') }))
  expect(screen.getByText(t('accessTokens.empty'))).toBeInTheDocument()
})

it('revokes a token after confirmation and keeps it listed as revoked', async () => {
  vi.spyOn(accessTokens, 'list').mockResolvedValue([{ ...existing, expires_at: null, is_active: true }])
  const revoke = vi.spyOn(accessTokens, 'revoke').mockResolvedValue()
  const { user } = renderWithProviders(<AccessTokensDialog open onClose={vi.fn()} />)

  await screen.findByText(existing.name)
  const row = rowOf(existing.name)
  await user.click(within(row).getByRole('button', { name: t('accessTokens.revoke') }))
  expect(revoke).not.toHaveBeenCalled()
  await user.click(within(row).getByRole('button', { name: t('accessTokens.revoke') }))

  expect(revoke).toHaveBeenCalledWith(existing.id)
  expect(within(row).getByText(t('accessTokens.revokedBadge'))).toBeInTheDocument()
  expect(within(row).queryByRole('button', { name: t('accessTokens.revoke') })).not.toBeInTheDocument()
})
