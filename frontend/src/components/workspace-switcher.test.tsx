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

afterEach(() => vi.restoreAllMocks())

it('opens the help documentation in a new tab from the account menu', async () => {
  const open = vi.spyOn(window, 'open').mockImplementation(() => null)
  const user = userEvent.setup()
  renderWithProviders(
    <WorkspaceSwitcher
      onChangePassword={vi.fn()}
      onTwoFactor={vi.fn()}
      onPasskeys={vi.fn()}
      onBackup={vi.fn()}
      onUpdateAvailable={vi.fn()}
      agentsEnabled={false}
      localAuthEnabled
    />,
  )

  await user.click(screen.getByRole('button', { name: /Personal/ }))
  await user.click(await screen.findByRole('menuitem', { name: t('nav.documentation') }))

  expect(open).toHaveBeenCalledWith(DOCS_URL, '_blank', 'noopener,noreferrer')
})
