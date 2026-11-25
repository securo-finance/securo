import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

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

afterEach(() => {
  vi.restoreAllMocks()
  navigate.mockReset()
  flags.pluginsEnabled = false
})

function renderSwitcher() {
  return renderWithProviders(
    <WorkspaceSwitcher
      onChangePassword={vi.fn()}
      onTwoFactor={vi.fn()}
      onPasskeys={vi.fn()}
      onBackup={vi.fn()}
      onAccessLog={vi.fn()}
      onUpdateAvailable={vi.fn()}
      agentsEnabled={false}
      localAuthEnabled
    />,
  )
}

it('opens the help documentation in a new tab from the account menu', async () => {
  const open = vi.spyOn(window, 'open').mockImplementation(() => null)
  const user = userEvent.setup()
  renderWithProviders(
    <WorkspaceSwitcher
      onChangePassword={vi.fn()}
      onTwoFactor={vi.fn()}
      onPasskeys={vi.fn()}
      onBackup={vi.fn()}
      onAccessLog={vi.fn()}
      onUpdateAvailable={vi.fn()}
      agentsEnabled={false}
      localAuthEnabled
    />,
  )

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
