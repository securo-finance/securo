import '@testing-library/jest-dom/vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorkspaceSettingsPage from '@/pages/workspace-settings'
import { renderWithProviders, t } from '@/test/utils'

const state = vi.hoisted(() => ({
  current: {
    id: 'ws-qualification', name: 'Qualification workspace', role: 'owner',
    default_currency: 'USD', locale: 'en', timezone: null, tax_jurisdiction: null,
    icon: 'briefcase', color: '#6366F1', review_policy: { ingest: 'needs_review', manual: 'reviewed' },
  },
  update: vi.fn(), canManage: true,
}))
vi.mock('@/contexts/workspace-context', async (load) => ({
  ...await load<typeof import('@/contexts/workspace-context')>(),
  useWorkspace: () => ({ current: state.current, canManage: state.canManage,
    workspaces: [state.current], refresh: vi.fn(), switchWorkspace: vi.fn() }),
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { id: 'owner', preferences: {} }, updateUser: vi.fn() }) }))
vi.mock('@/lib/api', () => ({
  workspaces: { update: state.update, listMembers: vi.fn().mockResolvedValue([]), stats: vi.fn().mockResolvedValue({ members: 1, accounts: 0, transactions: 0 }) },
  currencies: { list: vi.fn().mockResolvedValue(['USD']) },
  fiscal: { jurisdictions: vi.fn().mockResolvedValue([]) },
  auth: { oidcConfig: vi.fn().mockResolvedValue({ local_auth_enabled: false }), me: vi.fn() },
  timezones: { list: vi.fn().mockResolvedValue({ available: ['UTC'], default: 'UTC' }) },
}))
function policySection() {
  return screen.getByRole('heading', { name: t('workspace.reviewPolicyTitle') }).closest('section')!
}
function controls() {
  return {
    ingest: screen.getByRole('combobox', { name: t('workspace.reviewPolicyIngest') }),
    manual: screen.getByRole('combobox', { name: t('workspace.reviewPolicyManual') }),
    save: within(policySection()).getByRole('button', { name: t('common.save') }),
  }
}
describe('qualification: same-workspace policy refresh', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.canManage = true
    state.current = { ...state.current, review_policy: { ingest: 'needs_review', manual: 'reviewed' } }
    state.update.mockImplementation(async (_id, payload) => ({ ...state.current, ...payload }))
  })
  it('clean controls follow policy-only refresh and do not become falsely dirty', async () => {
    const view = renderWithProviders(<WorkspaceSettingsPage />)
    expect(controls().save).toBeDisabled()
    state.current = { ...state.current, review_policy: { ingest: 'reviewed', manual: 'needs_review' } }
    view.rerender(<WorkspaceSettingsPage />)
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyReviewed'))
    expect(controls().manual).toHaveTextContent(t('workspace.reviewPolicyNeedsReview'))
    expect(controls().save).toBeDisabled()
    expect(state.update).not.toHaveBeenCalled()
  })
  it('dirty refresh may reconcile or block, but cannot silently submit the stale peer field', async () => {
    const view = renderWithProviders(<WorkspaceSettingsPage />)
    await view.user.click(controls().ingest)
    await view.user.click(await screen.findByRole('option', { name: t('workspace.reviewPolicyReviewed') }))
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyReviewed'))
    // Another owner changes only manual; retain the same workspace and all formKey fields.
    state.current = { ...state.current, review_policy: { ingest: 'needs_review', manual: 'needs_review' } }
    view.rerender(<WorkspaceSettingsPage />)
    await view.user.click(controls().save)
    // Design-neutral safety invariant: blocked save or explicit reconciliation
    // is acceptable. Current server replaces the entire nested policy: omitting
    // manual is NOT safe without an independently approved server merge change.
    for (const [, payload] of state.update.mock.calls) {
      expect(payload.review_policy?.manual).toBe('needs_review')
    }
  })
  it('policy refresh preserves an unrelated dirty name and general refresh preserves dirty policy', async () => {
    const view = renderWithProviders(<WorkspaceSettingsPage />)
    const name = document.getElementById('ws-name') as HTMLInputElement
    expect(name).not.toBeNull()
    fireEvent.change(name, { target: { value: 'Unsaved name' } })
    await view.user.click(controls().ingest)
    await view.user.click(await screen.findByRole('option', { name: t('workspace.reviewPolicyReviewed') }))
    state.current = { ...state.current, review_policy: { ingest: 'needs_review', manual: 'needs_review' } }
    view.rerender(<WorkspaceSettingsPage />)
    expect(name.value).toBe('Unsaved name')
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyReviewed'))
    state.current = { ...state.current, name: 'External name' }
    view.rerender(<WorkspaceSettingsPage />)
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyReviewed'))
    await view.user.click(controls().save)
    await waitFor(() => expect(state.update).toHaveBeenCalledExactlyOnceWith(state.current.id, {
      review_policy: { ingest: 'reviewed', manual: 'needs_review' },
      expected_review_policy: { ingest: 'needs_review', manual: 'needs_review' },
    }))
  })
  it('concurrent same-key binary edits converge rather than inventing a third value', async () => {
    const view = renderWithProviders(<WorkspaceSettingsPage />)
    await view.user.click(controls().ingest)
    await view.user.click(await screen.findByRole('option', { name: t('workspace.reviewPolicyReviewed') }))
    state.current = { ...state.current, review_policy: { ingest: 'reviewed', manual: 'reviewed' } }
    view.rerender(<WorkspaceSettingsPage />)
    expect(controls().save).toBeDisabled()
  })
  it('edits made during save survive the returned acknowledgement', async () => {
    let finish!: (value: unknown) => void
    state.update.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const view = renderWithProviders(<WorkspaceSettingsPage />)
    await view.user.click(controls().ingest)
    await view.user.click(await screen.findByRole('option', { name: t('workspace.reviewPolicyReviewed') }))
    await view.user.click(controls().save)
    await waitFor(() => expect(state.update).toHaveBeenCalledTimes(1))
    expect(controls().save).toBeDisabled()
    await view.user.click(controls().ingest)
    await view.user.click(await screen.findByRole('option', { name: t('workspace.reviewPolicyNeedsReview') }))
    await act(async () => { finish({ ...state.current, review_policy: { ingest: 'reviewed', manual: 'reviewed' } }) })
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyNeedsReview'))
    await waitFor(() => expect(controls().save).toBeEnabled())
  })
  it('old workspace save cannot replace the new workspace draft', async () => {
    let finish!: (value: unknown) => void
    state.update.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const view = renderWithProviders(<WorkspaceSettingsPage />)
    await view.user.click(controls().ingest)
    await view.user.click(await screen.findByRole('option', { name: t('workspace.reviewPolicyReviewed') }))
    await view.user.click(controls().save)
    const old = state.current
    state.current = { ...state.current, id: 'ws-next', review_policy: { ingest: 'needs_review', manual: 'needs_review' } }
    view.rerender(<WorkspaceSettingsPage />)
    await act(async () => { finish({ ...old, review_policy: { ingest: 'reviewed', manual: 'reviewed' } }) })
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyNeedsReview'))
    expect(controls().manual).toHaveTextContent(t('workspace.reviewPolicyNeedsReview'))
    expect(controls().save).toBeDisabled()
  })
  it('409 retains edits and refreshes without automatically resubmitting', async () => {
    state.update.mockRejectedValue({ response: { status: 409, data: { detail: 'Review policy changed; refresh before saving' } } })
    const view = renderWithProviders(<WorkspaceSettingsPage />)
    await view.user.click(controls().ingest)
    await view.user.click(await screen.findByRole('option', { name: t('workspace.reviewPolicyReviewed') }))
    await view.user.click(controls().save)
    await waitFor(() => expect(controls().save).toBeEnabled())
    expect(state.update).toHaveBeenCalledTimes(1)
    expect(controls().ingest).toHaveTextContent(t('workspace.reviewPolicyReviewed'))
    state.current = { ...state.current, review_policy: { ingest: 'needs_review', manual: 'needs_review' } }
    view.rerender(<WorkspaceSettingsPage />)
    expect(controls().manual).toHaveTextContent(t('workspace.reviewPolicyNeedsReview'))
    expect(state.update).toHaveBeenCalledTimes(1)
  })
  it('control: viewer policy controls are read-only', async () => {
    state.canManage = false
    renderWithProviders(<WorkspaceSettingsPage />)
    expect(screen.getByRole('combobox', { name: t('workspace.reviewPolicyIngest') })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: t('workspace.reviewPolicyManual') })).toBeDisabled()
    expect(within(policySection()).queryByRole('button', { name: t('common.save') })).not.toBeInTheDocument()
  })
})
