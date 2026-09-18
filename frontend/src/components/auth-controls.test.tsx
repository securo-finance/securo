/**
 * A server can switch to OIDC-only after users have already enrolled TOTP or a
 * passkey. There are no recovery codes, so if the menu hides those entries the
 * enrolled factor becomes unremovable. These tests assert the removal paths stay
 * reachable on both menu surfaces while enrollment stays hidden.
 */
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { AppLayout } from './app-layout'
import { TwoFactorSetup } from './two-factor-setup'
import { renderWithProviders, t } from '@/test/utils'

const state = vi.hoisted(() => ({
  user: {
    email: 'synthetic@example.com',
    is_2fa_enabled: true,
    is_superuser: false,
    preferences: { onboarding_completed: true },
  },
  workspace: { id: 'synthetic', name: 'Synthetic workspace', kind: 'personal', role: 'owner' },
  auth: {
    oidcConfig: vi.fn(),
    listPasskeys: vi.fn(),
    deletePasskey: vi.fn(),
    disable2fa: vi.fn(),
    setup2fa: vi.fn(),
    enable2fa: vi.fn(),
    registerPasskeyOptions: vi.fn(),
    verifyPasskeyRegistration: vi.fn(),
  },
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({
    user: state.user,
    logout: vi.fn(),
    updateUser: (user: typeof state.user) => {
      state.user = user
    },
  }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({
    current: state.workspace,
    workspaces: [state.workspace],
    switchWorkspace: vi.fn(),
    refresh: vi.fn(),
    hasModule: () => false,
    canWrite: false,
    isLoading: false,
  }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null }),
}))
vi.mock('@/hooks/use-feature-flags', () => ({
  useFeatureFlags: () => ({ isLoading: false, agentsEnabled: false }),
}))
vi.mock('@/lib/api', () => ({
  auth: state.auth,
  admin: {
    defaultColors: async () => ({ light: null, dark: null }),
    numberFormat: async () => ({ format: 'auto' }),
  },
  accounts: { list: async () => [] },
  workspaces: { create: vi.fn() },
  info: { get: async () => ({ features: {} }) },
}))
vi.mock('@/lib/webauthn', () => ({
  passkeyBlocker: () => null,
  passkeyFailure: () => 'unknown',
  startPasskeyRegistration: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('next-themes', () => ({ useTheme: () => ({ theme: 'light', setTheme: vi.fn() }) }))
vi.mock('@/components/collection-selector', () => ({ CollectionSelector: () => null }))
vi.mock('@/components/command-palette', () => ({ CommandPalette: () => null }))
vi.mock('@/components/global-chat-panel', () => ({ GlobalChatPanel: () => null }))
vi.mock('@/components/update-available-banner', () => ({ UpdateAvailableBanner: () => null }))
vi.mock('@/components/update-available-dialog', () => ({ UpdateAvailableDialog: () => null }))
vi.mock('@/components/backup-dialog', () => ({ BackupDialog: () => null }))

/** The sidebar switcher (desktop) and the header avatar (mobile) are separate menus. */
function openMenu(surface: string) {
  return surface === 'mobile'
    ? screen.getByRole('button', { name: t('common.userMenu') })
    : screen.getByRole('button', { name: /Synthetic workspace/ })
}

beforeEach(() => {
  vi.clearAllMocks()
  state.user = { ...state.user, is_2fa_enabled: true }
  state.auth.oidcConfig.mockResolvedValue({
    enabled: true,
    local_auth_enabled: false,
    provider_name: 'SSO',
  })
  state.auth.listPasskeys.mockResolvedValue([
    { id: 'synthetic-key', name: 'Saved key', created_at: '2026-01-01T00:00:00Z', last_used_at: null },
  ])
  state.auth.disable2fa.mockResolvedValue({})
  state.auth.deletePasskey.mockResolvedValue(undefined)
})

it.each(['desktop', 'mobile'])(
  'keeps 2FA and passkey removal reachable from the %s menu in OIDC-only mode',
  async (surface) => {
    const { user } = renderWithProviders(<AppLayout />)
    await waitFor(() => expect(state.auth.oidcConfig).toHaveBeenCalled())

    await user.click(openMenu(surface))
    expect(
      screen.queryByRole('menuitem', { name: t('auth.changePassword') }),
    ).not.toBeInTheDocument()

    await user.click(screen.getByRole('menuitem', { name: t('auth.disable2fa') }))
    const twoFactorDialog = screen.getByRole('dialog')
    await user.type(within(twoFactorDialog).getByLabelText(t('auth.password')), 'synthetic-password')
    await user.type(within(twoFactorDialog).getByLabelText(t('auth.twoFactor')), '123456')
    await user.click(within(twoFactorDialog).getByRole('button', { name: t('auth.disable2fa') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(state.auth.disable2fa).toHaveBeenCalledWith('synthetic-password', '123456')

    // The entry retires with the factor it removed.
    await user.click(openMenu(surface))
    expect(screen.queryByRole('menuitem', { name: t('auth.disable2fa') })).not.toBeInTheDocument()

    await user.click(screen.getByRole('menuitem', { name: t('auth.passkeysTitle') }))
    await screen.findByText('Saved key')
    expect(screen.getByText(t('auth.passkeysCleanupDescription'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('auth.addPasskey') })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('auth.deletePasskey') }))
    await user.click(screen.getByRole('button', { name: t('common.delete') }))
    expect(await screen.findByText(t('auth.noPasskeys'))).toBeInTheDocument()
    expect(state.auth.deletePasskey).toHaveBeenCalledWith('synthetic-key')

    expect(state.auth.setup2fa).not.toHaveBeenCalled()
    expect(state.auth.enable2fa).not.toHaveBeenCalled()
    expect(state.auth.registerPasskeyOptions).not.toHaveBeenCalled()
  },
)

it.each(['desktop', 'mobile'])(
  'still offers the full local credential menu on the %s surface',
  async (surface) => {
    state.auth.oidcConfig.mockResolvedValue({ enabled: false, local_auth_enabled: true })
    state.user = { ...state.user, is_2fa_enabled: false }
    const { user } = renderWithProviders(<AppLayout />)
    await waitFor(() => expect(state.auth.oidcConfig).toHaveBeenCalled())

    await user.click(openMenu(surface))
    expect(screen.getByRole('menuitem', { name: t('auth.changePassword') })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: t('auth.twoFactorTitle') })).toBeInTheDocument()

    await user.click(screen.getByRole('menuitem', { name: t('auth.passkeysTitle') }))
    await screen.findByText('Saved key')
    expect(screen.getByRole('button', { name: t('auth.addPasskey') })).toBeInTheDocument()
  },
)

it('never offers TOTP enrollment once the enrolled factor is gone', () => {
  state.user = { ...state.user, is_2fa_enabled: false }
  renderWithProviders(<TwoFactorSetup open localAuthEnabled={false} onClose={vi.fn()} />)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(state.auth.setup2fa).not.toHaveBeenCalled()
})
