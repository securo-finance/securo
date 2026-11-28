import { screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { AccessTokensDialog } from '@/components/access-tokens-dialog'
import type { InternalAxiosRequestConfig } from 'axios'

import { WORKSPACE_STORAGE_KEY, accessTokens, api, workspaces } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { AccessToken, AccessTokenCreated, WorkspaceMember } from '@/types'

const workspace = vi.hoisted(() => ({ canWrite: true }))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ current: { id: 'ws-1', name: 'Home' }, canWrite: workspace.canWrite }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: 'user-1' } }),
}))

vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'pt-BR' }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'America/Los_Angeles' }))

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
  // A long name truncates, so the full one is in the tooltip.
  expect(within(row).getByText(existing.name)).toHaveAttribute('title', existing.name)
})

it('lists a resource once when a token carries both its read and write scopes', async () => {
  vi.spyOn(accessTokens, 'list').mockResolvedValue([
    { ...existing, scopes: ['accounts:read', 'accounts:write', 'reports:read'] },
  ])
  renderWithProviders(<AccessTokensDialog open onClose={vi.fn()} />)

  await screen.findByText(existing.name)
  const row = rowOf(existing.name)
  expect(within(row).getByText(t('plugins.code.write', { what: 'accounts' }))).toBeInTheDocument()
  expect(within(row).getByText(t('plugins.code.read', { what: 'reports' }))).toBeInTheDocument()
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

  expect(create).toHaveBeenCalledWith(
    { name: 'CI', scopes: ['accounts:write', 'transactions:read'], expires_in_days: 90 },
    'ws-1',
  )
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
  expect(create).toHaveBeenCalledWith(
    { name: 'All', scopes: ['accounts:write', 'transactions:write', 'reports:read'], expires_in_days: 90 },
    'ws-1',
  )
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

  expect(revoke).toHaveBeenCalledWith(existing.id, 'ws-1')
  expect(within(row).getByText(t('accessTokens.revokedBadge'))).toBeInTheDocument()
  expect(within(row).queryByRole('button', { name: t('accessTokens.revoke') })).not.toBeInTheDocument()
})

it("formats token dates in the display locale, on the workspace timezone's day", async () => {
  // 01:00 UTC on the 25th is still the 24th in Los Angeles.
  vi.spyOn(accessTokens, 'list').mockResolvedValue([
    { ...existing, last_used_at: '2026-11-25T01:00:00Z' },
  ])
  renderWithProviders(<AccessTokensDialog open onClose={vi.fn()} />)

  await screen.findByText(existing.name)
  expect(within(rowOf(existing.name)).getByText('24 de nov. de 2026')).toBeInTheDocument()
  expect(within(rowOf(existing.name)).getByText('31 de dez. de 2019')).toBeInTheDocument()
})

it('stays open while a token is being created', async () => {
  vi.spyOn(accessTokens, 'list').mockResolvedValue([])
  let finish: (token: AccessTokenCreated) => void = () => {}
  vi.spyOn(accessTokens, 'create').mockReturnValue(
    new Promise<AccessTokenCreated>((resolve) => {
      finish = resolve
    }),
  )
  const onClose = vi.fn()
  const { user } = renderWithProviders(<AccessTokensDialog open onClose={onClose} />)

  await screen.findByText(t('accessTokens.empty'))
  await user.click(screen.getByRole('button', { name: t('accessTokens.newToken') }))
  await user.type(screen.getByLabelText(t('accessTokens.name')), 'CI')
  await user.click(scopeBox('accounts', 'scopeRead'))
  await user.click(screen.getByRole('button', { name: t('accessTokens.create') }))

  await user.keyboard('{Escape}')
  expect(onClose).not.toHaveBeenCalled()

  finish({ ...existing, id: 'tok-3', name: 'CI', expires_at: null, token: 'sct_kept' })
  expect(await screen.findByDisplayValue('sct_kept')).toBeInTheDocument()

  await user.keyboard('{Escape}')
  expect(onClose).toHaveBeenCalled()
})

it('keeps every request in the workspace on screen after another tab switches', async () => {
  // The real client runs here, so the workspace header is the one sent.
  vi.restoreAllMocks()
  const active: AccessToken = { ...existing, expires_at: null, is_active: true }
  const sent: Record<string, string | undefined> = {}
  const originalAdapter = api.defaults.adapter
  api.defaults.adapter = (config: InternalAxiosRequestConfig) => {
    const method = (config.method ?? 'get').toUpperCase()
    sent[`${method} ${config.url}`] = config.headers.get('X-Workspace-Id')?.toString()
    const data =
      config.url === '/access-tokens/scopes'
        ? { scopes: SCOPES }
        : method === 'GET'
          ? [active]
          : method === 'POST'
            ? { ...active, id: 'tok-4', name: 'CI', token: 'sct_pinned' }
            : undefined
    return Promise.resolve({ data, status: 200, statusText: 'OK', headers: {}, config })
  }
  localStorage.setItem(WORKSPACE_STORAGE_KEY, 'ws-1')

  try {
    const { user } = renderWithProviders(<AccessTokensDialog open onClose={vi.fn()} />)
    await screen.findByText(active.name)
    await user.click(screen.getByRole('button', { name: t('accessTokens.newToken') }))
    await user.type(screen.getByLabelText(t('accessTokens.name')), 'CI')
    await user.click(scopeBox('accounts', 'scopeRead'))

    // Another tab switches workspace while the form is open.
    localStorage.setItem(WORKSPACE_STORAGE_KEY, 'ws-2')
    await user.click(screen.getByRole('button', { name: t('accessTokens.create') }))
    await screen.findByDisplayValue('sct_pinned')

    const row = rowOf(active.name)
    await user.click(within(row).getByRole('button', { name: t('accessTokens.revoke') }))
    await user.click(within(row).getByRole('button', { name: t('accessTokens.revoke') }))
    await within(row).findByText(t('accessTokens.revokedBadge'))

    expect(sent).toMatchObject({
      'GET /access-tokens': 'ws-1',
      'POST /access-tokens': 'ws-1',
      [`DELETE /access-tokens/${active.id}`]: 'ws-1',
    })
  } finally {
    api.defaults.adapter = originalAdapter
    localStorage.removeItem(WORKSPACE_STORAGE_KEY)
  }
})
