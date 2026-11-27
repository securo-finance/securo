import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { DOCS_URL, WorkspaceSwitcher } from '@/components/workspace-switcher'
import { renderWithProviders, t } from '@/test/utils'

const WORKSPACE = {
  id: 'ws-1',
  name: 'Personal',
  kind: 'personal',
  is_archived: false,
  icon: 'briefcase',
  color: '#6366F1',
  role: 'owner',
}

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({
    current: WORKSPACE,
    workspaces: [WORKSPACE],
    switchWorkspace: vi.fn(),
    refresh: vi.fn(),
  }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({
    user: { id: 'user-1', email: 'someone@example.com', is_superuser: false, is_2fa_enabled: false },
    logout: vi.fn(),
  }),
}))

const flags = vi.hoisted(() => ({ pluginsEnabled: false }))
vi.mock('@/hooks/use-feature-flags', () => ({
  useFeatureFlags: () => ({ isLoading: false, agentsEnabled: false, pluginsEnabled: flags.pluginsEnabled }),
}))

const navigate = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}))

const api = vi.hoisted(() => ({ mine: vi.fn(), create: vi.fn(), toastError: vi.fn() }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  quotas: { mine: api.mine },
  workspaces: { create: api.create },
}))
vi.mock('sonner', () => ({ toast: { error: api.toastError, success: vi.fn() } }))

const NO_LIMITS = { enforced: false, info_url: null, counters: [] }

beforeEach(() => {
  api.mine.mockResolvedValue(NO_LIMITS)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.resetAllMocks()
  navigate.mockReset()
  flags.pluginsEnabled = false
})

function renderSwitcher(onUsageLimits = vi.fn()) {
  return renderWithProviders(
    <WorkspaceSwitcher
      onChangePassword={vi.fn()}
      onTwoFactor={vi.fn()}
      onPasskeys={vi.fn()}
      onBackup={vi.fn()}
      onAccessLog={vi.fn()}
      onUsageLimits={onUsageLimits}
      onUpdateAvailable={vi.fn()}
      agentsEnabled={false}
      localAuthEnabled
    />,
  )
}

async function openCreateDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /Personal/ }))
  await user.click(await screen.findByRole('menuitem', { name: t('workspace.create') }))
  await user.type(await screen.findByLabelText(t('common.name')), 'Side project')
}

it('lists the usage limits in the account menu only when the account has some', async () => {
  const onUsageLimits = vi.fn()
  api.mine.mockResolvedValue({
    enforced: true,
    info_url: null,
    counters: [{ counter: 'max_bank_connections', current: 1, maximum: 3 }],
  })
  const user = userEvent.setup()
  renderSwitcher(onUsageLimits)

  await waitFor(() => expect(api.mine).toHaveBeenCalled())
  await user.click(screen.getByRole('button', { name: /Personal/ }))
  await user.click(await screen.findByRole('menuitem', { name: t('quota.menu') }))

  expect(onUsageLimits).toHaveBeenCalled()
})

it('leaves the usage limits out of the account menu without limits', async () => {
  const user = userEvent.setup()
  renderSwitcher()

  await waitFor(() => expect(api.mine).toHaveBeenCalled())
  await user.click(screen.getByRole('button', { name: /Personal/ }))
  await screen.findByRole('menuitem', { name: t('nav.documentation') })

  expect(screen.queryByRole('menuitem', { name: t('quota.menu') })).not.toBeInTheDocument()
})

it('says which limit filled when the new workspace is refused', async () => {
  api.create.mockRejectedValue({
    isAxiosError: true,
    response: {
      status: 403,
      data: { detail: { code: 'quota_exceeded', counter: 'max_business_workspaces', current: 1, maximum: 1, message: 'x' } },
    },
  })
  const user = userEvent.setup()
  renderSwitcher()

  await openCreateDialog(user)
  await user.click(screen.getByRole('button', { name: t('common.create') }))

  await waitFor(() =>
    expect(api.toastError).toHaveBeenCalledWith(
      t('quota.reached.max_business_workspaces', { maximum: 1 }),
      undefined,
    ),
  )
})

it('stops before the attempt when the chosen kind is already full', async () => {
  api.mine.mockResolvedValue({
    enforced: true,
    info_url: null,
    counters: [{ counter: 'max_personal_workspaces', current: 1, maximum: 1 }],
  })
  const user = userEvent.setup()
  renderSwitcher()

  await waitFor(() => expect(api.mine).toHaveBeenCalled())
  await openCreateDialog(user)

  expect(await screen.findByRole('alert')).toHaveTextContent(
    t('quota.reached.max_personal_workspaces', { maximum: 1 }),
  )
  expect(screen.queryByRole('link', { name: t('quota.learnMore') })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: t('common.create') })).toBeDisabled()

  // The other kind still has room.
  await user.click(screen.getByRole('button', { name: t('workspace.kindBusiness') }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: t('common.create') })).toBeEnabled()
  expect(api.create).not.toHaveBeenCalled()
})

it('opens the help documentation in a new tab from the account menu', async () => {
  const open = vi.spyOn(window, 'open').mockImplementation(() => null)
  const user = userEvent.setup()
  renderSwitcher()

  await user.click(screen.getByRole('button', { name: /Personal/ }))
  await user.click(await screen.findByRole('menuitem', { name: t('nav.documentation') }))

  expect(open).toHaveBeenCalledWith(DOCS_URL, '_blank', 'noopener,noreferrer')
})

it('opens the plugin catalog from the account menu when plugins are on', async () => {
  flags.pluginsEnabled = true
  const user = userEvent.setup()
  renderSwitcher()

  await user.click(screen.getByRole('button', { name: /Personal/ }))
  await user.click(await screen.findByRole('menuitem', { name: t('plugins.title') }))

  expect(navigate).toHaveBeenCalledWith('/plugins')
})

it('leaves plugins out of the account menu when they are off', async () => {
  const user = userEvent.setup()
  renderSwitcher()

  await user.click(screen.getByRole('button', { name: /Personal/ }))
  await screen.findByRole('menuitem', { name: t('nav.documentation') })

  expect(screen.queryByRole('menuitem', { name: t('plugins.title') })).not.toBeInTheDocument()
})
