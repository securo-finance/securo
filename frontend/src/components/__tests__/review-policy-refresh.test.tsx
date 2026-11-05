import { act, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkspaceProvider } from '@/contexts/workspace-provider'
import { useWorkspace } from '@/contexts/workspace-context'
import WorkspaceSettingsPage from '@/pages/workspace-settings'
import { renderWithProviders, t } from '@/test/utils'
import type { Workspace } from '@/types'

const mocks = vi.hoisted(() => ({
  list: vi.fn(), update: vi.fn(),
  auth: { user: { id: 'owner', preferences: {} } as { id: string, preferences: object } | null,
    token: 'synthetic-session' as string | null, isLoading: false, updateUser: vi.fn() },
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => mocks.auth }))
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  workspaces: { list: mocks.list, update: mocks.update, listMembers: vi.fn().mockResolvedValue([]),
    stats: vi.fn().mockResolvedValue({ members: 1, accounts: 0, transactions: 0 }) },
  currencies: { list: vi.fn().mockResolvedValue(['USD']) },
  fiscal: { jurisdictions: vi.fn().mockResolvedValue([]) },
  auth: { oidcConfig: vi.fn().mockResolvedValue({ local_auth_enabled: false }), me: vi.fn() },
  timezones: { list: vi.fn().mockResolvedValue({ available: ['UTC'], default: 'UTC' }) },
}))
const baseline = { ingest: 'needs_review', manual: 'reviewed' } as const
const workspace = {
  id: 'same-id', name: 'Same-auth workspace', kind: 'personal', role: 'owner', enabled_modules: [],
  is_archived: false, created_at: '2026-11-05T00:00:00Z', created_by_user_id: 'owner', managed_by_user_id: null,
  default_currency: 'USD', locale: 'en', timezone: null, tax_jurisdiction: null,
  icon: 'briefcase', color: '#6366F1', review_policy: baseline,
} as Workspace
function Harness() {
  const context = useWorkspace()
  return <>
    <button onClick={() => { void context.refresh() }}>Refresh workspaces</button>
    <output data-testid="authority">{context.canManage ? 'manage' : 'none'}</output>
    <WorkspaceSettingsPage />
  </>
}
function Tree() { return <WorkspaceProvider><Harness /></WorkspaceProvider> }
function controls() {
  const section = screen.getByRole('heading', { name: t('workspace.reviewPolicyTitle') }).closest('section')!
  return {
    ingest: screen.getByRole('combobox', { name: t('workspace.reviewPolicyIngest') }),
    manual: screen.getByRole('combobox', { name: t('workspace.reviewPolicyManual') }),
    save: within(section).getByRole('button', { name: t('common.save') }),
  }
}
async function dirty() {
  const view = renderWithProviders(<Tree />)
  await screen.findByRole('heading', { name: t('workspace.reviewPolicyTitle') })
  await view.user.click(controls().ingest)
  await view.user.click(await screen.findByRole('option', { name: t('workspace.reviewPolicyReviewed') }))
  return view
}

describe('actual provider and settings refresh boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mocks.auth.user = { id: 'owner', preferences: {} }
    mocks.auth.token = 'synthetic-session'
    mocks.list.mockReset().mockResolvedValue([{ ...workspace }])
    mocks.update.mockReset().mockImplementation(async (_id, payload) => ({ ...workspace, ...payload }))
  })
  it('real 409 and failed provider refresh preserve draft, block retry, then reconcile before explicit resave', async () => {
    const view = await dirty()
    mocks.update.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'Policy changed' } } })
    mocks.list.mockRejectedValueOnce(new Error('Private upstream diagnostic'))
    await view.user.click(controls().save)
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyReviewed')))
    expect(controls().save).toBeDisabled()
    expect(screen.getByRole('alert')).not.toHaveTextContent('Private upstream diagnostic')
    await view.user.click(controls().save)
    expect(mocks.update).toHaveBeenCalledTimes(1)
    mocks.list.mockResolvedValueOnce([{ ...workspace, review_policy: { ingest: 'needs_review', manual: 'needs_review' } }])
    await view.user.click(screen.getByRole('button', { name: 'Refresh workspace' }))
    await waitFor(() => expect(controls().manual).toHaveTextContent(t('workspace.reviewPolicyNeedsReview')))
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyReviewed'))
    await waitFor(() => expect(controls().save).toBeEnabled())
    expect(mocks.update).toHaveBeenCalledTimes(1)
    await view.user.click(controls().save)
    await waitFor(() => expect(mocks.update).toHaveBeenCalledTimes(2))
    expect(mocks.update).toHaveBeenLastCalledWith(workspace.id, {
      review_policy: { ingest: 'reviewed', manual: 'needs_review' },
      expected_review_policy: { ingest: 'needs_review', manual: 'needs_review' },
    })
  })
  it('an ordinary failed same-auth refresh also retains dirty draft and fences save until reconciliation', async () => {
    const view = await dirty()
    mocks.list.mockRejectedValueOnce(new Error('offline'))
    await view.user.click(screen.getByRole('button', { name: 'Refresh workspaces' }))
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(controls().save).toBeDisabled())
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyReviewed'))
    expect(mocks.update).not.toHaveBeenCalled()
    mocks.list.mockResolvedValueOnce([{ ...workspace, review_policy: { ingest: 'reviewed', manual: 'reviewed' } }])
    await view.user.click(screen.getByRole('button', { name: 'Refresh workspaces' }))
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(3))
    expect(controls().save).toBeDisabled() // D == S converges, no automatic write.
    expect(mocks.update).not.toHaveBeenCalled()
  })
  it.each(['identity change', 'logout'])('never retains data, authority or drafts across %s, including a late old response', async (transition) => {
    const view = await dirty()
    let finish!: (value: Workspace[]) => void
    mocks.list.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await view.user.click(screen.getByRole('button', { name: 'Refresh workspaces' }))
    mocks.list.mockRejectedValueOnce(new Error('new auth initial failure'))
    mocks.auth.user = transition === 'logout' ? null : { id: 'different-owner', preferences: {} }
    mocks.auth.token = transition === 'logout' ? null : 'different-session'
    view.rerender(<Tree />)
    await waitFor(() => expect(screen.queryByRole('heading', { name: t('workspace.reviewPolicyTitle') })).not.toBeInTheDocument())
    expect(screen.getByTestId('authority')).toHaveTextContent('none')
    await act(async () => { finish([{ ...workspace }]) })
    expect(screen.queryByRole('heading', { name: t('workspace.reviewPolicyTitle') })).not.toBeInTheDocument()
    if (transition === 'logout') {
      mocks.auth.user = { id: 'owner', preferences: {} }
      mocks.auth.token = 'new-login'
      mocks.list.mockReset().mockResolvedValue([{ ...workspace }])
      view.rerender(<Tree />)
    } else {
      mocks.list.mockResolvedValueOnce([{ ...workspace }])
      await view.user.click(screen.getByRole('button', { name: 'Refresh workspaces' }))
    }
    await screen.findByRole('heading', { name: t('workspace.reviewPolicyTitle') })
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyNeedsReview'))
    expect(controls().save).toBeDisabled()
    expect(mocks.update).not.toHaveBeenCalled()
  })
})
