import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import { AppLayout } from './app-layout'
import { SUPPORTED_LANGS } from '@/lib/i18n'
import { renderWithProviders, t, i18n } from '@/test/utils'
import type { Account } from '@/types'

// Only IO/providers and unrelated shell panels are substituted. AppLayout's
// Collection intersection, reducer, deduplication, rows and formatting are real.
const state = vi.hoisted(() => ({
  accounts: [] as Account[],
  activeAccountIds: null as string[] | null,
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({
    user: {
      email: 'sidebar@example.test',
      preferences: { onboarding_completed: true, currency_display: 'USD' },
    },
    logout: vi.fn(),
    updateUser: vi.fn(),
  }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ hasModule: () => false, canWrite: false, isLoading: false }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: state.activeAccountIds }),
}))
vi.mock('@/hooks/use-feature-flags', () => ({
  useFeatureFlags: () => ({ agentsEnabled: false }),
}))
vi.mock('@/hooks/use-local-auth', () => ({ useLocalAuthEnabled: () => false }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US' }))
vi.mock('next-themes', () => ({ useTheme: () => ({ theme: 'light', setTheme: vi.fn() }) }))
vi.mock('@/lib/api', () => ({
  auth: {},
  admin: { defaultColors: async () => ({ light: null, dark: null }) },
  accounts: { list: async () => state.accounts },
}))
vi.mock('@/components/collection-selector', () => ({ CollectionSelector: () => null }))
vi.mock('@/components/workspace-switcher', () => ({ WorkspaceSwitcher: () => null }))
vi.mock('@/components/command-palette', () => ({ CommandPalette: () => null }))
vi.mock('@/components/global-chat-panel', () => ({ GlobalChatPanel: () => null }))
vi.mock('@/components/update-available-banner', () => ({ UpdateAvailableBanner: () => null }))
vi.mock('@/components/update-available-dialog', () => ({ UpdateAvailableDialog: () => null }))
vi.mock('@/components/backup-dialog', () => ({ BackupDialog: () => null }))
vi.mock('@/components/two-factor-setup', () => ({ TwoFactorSetup: () => null }))
vi.mock('@/components/passkey-management-dialog', () => ({ PasskeyManagementDialog: () => null }))
vi.mock('@/components/change-password-dialog', () => ({ ChangePasswordDialog: () => null }))

function account(id: string, amount: number, overrides: Partial<Account> = {}): Account {
  return {
    id, name: id, user_id: 'synthetic-user', connection_id: null,
    external_id: null, display_name: null, masked_number: null,
    institution_name: null, institution_logo_url: null, type: 'checking',
    balance: amount, current_balance: amount, balance_primary: amount,
    previous_balance: null, currency: 'USD', credit_limit: null,
    available_credit: null, statement_close_day: null, payment_due_day: null,
    next_close_date: null, next_due_date: null, minimum_payment: null,
    card_brand: null, card_level: null, shared_balance_group: null,
    is_closed: false, closed_at: null, exclude_from_reports: false,
    ...overrides,
  }
}

function sidebar() {
  return within(screen.getByRole('complementary'))
}

async function header() {
  // Select by the existing heading, never by the expected monetary value.
  const title = await sidebar().findByText(t('accounts.title'))
  const button = title.closest('button')
  expect(button).not.toBeNull()
  return button!
}

function expectRow(id: string, balance: string) {
  const row = sidebar().getByRole('link', { name: new RegExp(`^${id}`) })
  expect(row).toBeVisible()
  expect(row).toHaveAttribute('href', `/accounts/${id}`)
  expect(within(row).getByText(balance)).toBeVisible()
}

function expectIncludedTotal(button: HTMLElement, amount: string) {
  expect(within(button).getByText(t('accounts.includedInTotals'))).toBeVisible()
  expect(within(button).getByText(amount)).toBeVisible()
}

it('localizes the included-totals qualifier in every shipped locale', async () => {
  const previousLanguage = i18n.language
  try {
    for (const { code } of SUPPORTED_LANGS) {
      await i18n.changeLanguage(code)
      const qualifier = i18n.t('accounts.includedInTotals')
      expect(qualifier, `${code} must define accounts.includedInTotals`)
        .not.toBe('accounts.includedInTotals')
      if (code !== 'en') expect(qualifier, `${code} must not fall back to English`)
        .not.toBe('Included in totals')
    }
  } finally {
    await i18n.changeLanguage(previousLanguage)
  }
})

it('visibly qualifies the subtotal and exposes that qualifier to keyboard users', async () => {
  const { user } = renderWithProviders(<AppLayout />)
  const button = await header()
  const qualifier = t('accounts.includedInTotals')

  expect(within(button).getByText(qualifier)).toBeVisible()
  expect(button).toHaveAccessibleName(new RegExp(qualifier))
  expect(button).toHaveAttribute('aria-expanded', 'true')
  button.focus()
  expect(button).toHaveFocus()
  await user.keyboard(' ')
  expect(button).toHaveAttribute('aria-expanded', 'false')
  expect(sidebar().queryByRole('link', { name: /^Checking/ })).not.toBeInTheDocument()
  await user.keyboard(' ')
  expect(button).toHaveAttribute('aria-expanded', 'true')
  expect(sidebar().getByRole('link', { name: /^Checking/ })).toBeVisible()
})

beforeEach(() => {
  state.activeAccountIds = null
  state.accounts = [
    account('Checking', 100),
    account('Mirror', 300, { exclude_from_reports: true }),
  ]
})

describe('sidebar account rollup — approved PR-5 contract', () => {
  it('excludes the mirror from the header but keeps both own-balance rows visible', async () => {
    renderWithProviders(<AppLayout />)
    const total = await header()
    expectRow('Checking', '$100.00')
    expectRow('Mirror', '$300.00')
    expectIncludedTotal(total, '$100.00')
  })

  it.each([false, true])('counts the included shared sibling once (included-first=%s)', async (includedFirst) => {
    const excluded = account('Flagged-card', 300, {
      type: 'credit_card', shared_balance_group: 'shared', exclude_from_reports: true,
    })
    const included = account('Included-card', 100, {
      type: 'credit_card', shared_balance_group: 'shared',
    })
    state.accounts = includedFirst ? [included, excluded] : [excluded, included]
    renderWithProviders(<AppLayout />)
    const total = await header()
    expectRow('Flagged-card', '$300.00')
    expectRow('Included-card', '$100.00')
    // Unequal synthetic balances distinguish filter-before-dedup from letting
    // the excluded first sibling claim the group's slot. No backend parity claim.
    expectIncludedTotal(total, '$100.00')
  })

  it('counts neither shared sibling when both are excluded', async () => {
    state.accounts = [
      account('Flagged-card', 300, { shared_balance_group: 'shared', exclude_from_reports: true }),
      account('Other-flagged-card', 100, { shared_balance_group: 'shared', exclude_from_reports: true }),
    ]
    renderWithProviders(<AppLayout />)
    expectIncludedTotal(await header(), '$0.00')
  })

  it.each([
    { scope: 'excluded only', ids: ['Mirror'], checking: false, mirror: true, total: '$0.00' },
    { scope: 'included only', ids: ['Checking'], checking: true, mirror: false, total: '$100.00' },
    { scope: 'both', ids: ['Checking', 'Mirror'], checking: true, mirror: true, total: '$100.00' },
    { scope: 'empty / wallet-only', ids: [], checking: false, mirror: false, total: '$0.00' },
  ])('intersects Collection membership and exclusion: $scope', async ({ ids, checking, mirror, total }) => {
    // This included account must never leak from outside the Collection.
    state.accounts.push(account('Outside', 700))
    state.activeAccountIds = ids
    renderWithProviders(<AppLayout />)
    const button = await header()
    expect(sidebar().queryByRole('link', { name: /^Checking/ }) !== null).toBe(checking)
    expect(sidebar().queryByRole('link', { name: /Mirror/ }) !== null).toBe(mirror)
    expect(sidebar().queryByRole('link', { name: /Outside/ })).not.toBeInTheDocument()
    expectIncludedTotal(button, total)
  })

  it('updates the real sidebar from an excluded-only Collection back to all accounts', async () => {
    state.activeAccountIds = ['Mirror']
    const view = renderWithProviders(<AppLayout />)
    const button = await header()
    expectRow('Mirror', '$300.00')
    expectIncludedTotal(button, '$0.00')
    state.activeAccountIds = null
    view.rerender(<AppLayout />)
    expectRow('Checking', '$100.00')
    expectRow('Mirror', '$300.00')
    expectIncludedTotal(button, '$100.00')
  })
})

describe('sidebar unchanged calculation and privacy baselines', () => {
  it.each([
    { ids: null, reverse: false, total: '$100.00' },
    { ids: null, reverse: true, total: '$250.00' },
    { ids: ['Second-card'], reverse: false, total: '$250.00' },
  ])('keeps first eligible shared-card semantics: %j', async ({ ids, reverse, total }) => {
    state.accounts = [
      account('First-card', 100, { shared_balance_group: 'shared' }),
      account('Second-card', 250, { shared_balance_group: 'shared' }),
    ]
    if (reverse) state.accounts.reverse()
    state.activeAccountIds = ids
    renderWithProviders(<AppLayout />)
    expectIncludedTotal(await header(), total)
  })

  it('preserves primary conversion, null fallback, zero primary, and negative styling', async () => {
    state.accounts = [
      account('Converted', 900, { currency: 'EUR', balance_primary: -150 }),
      account('Fallback', 25, { balance_primary: null }),
      account('Zero-primary', 700, { balance_primary: 0 }),
    ]
    renderWithProviders(<AppLayout />)
    const button = await header()
    expect(within(button).getByText(t('accounts.includedInTotals'))).toBeVisible()
    expect(within(button).getByText('-$125.00')).toHaveClass('text-rose-400')
    expectRow('Converted', '-$150.00')
    expectRow('Fallback', '$25.00')
    expectRow('Zero-primary', '$0.00')
  })

  it('masks header and row amounts while preserving the existing heading and collapse control', async () => {
    const { user } = renderWithProviders(<AppLayout />)
    const button = await header()
    await user.click(sidebar().getByRole('button', { name: t('privacy.hide') }))
    expect(within(button).getByText('•••••')).toBeVisible()
    expect(within(button).getByText(t('accounts.includedInTotals'))).toBeVisible()
    expect(button).toHaveAccessibleName(new RegExp(t('accounts.includedInTotals')))
    expect(button).not.toHaveAttribute('title')
    expect(button).not.toHaveAttribute('aria-label')
    expect(button).not.toHaveAccessibleName(/\$\d/)
    expect(sidebar().queryByText(/\$\d/)).not.toBeInTheDocument()
    const checkingRow = sidebar().getByRole('link', { name: /^Checking/ })
    const mirrorRow = sidebar().getByRole('link', { name: /Mirror/ })
    expect(checkingRow).toBeVisible()
    expect(mirrorRow).toBeVisible()
    expect(checkingRow).not.toHaveAccessibleName(/\$\d/)
    expect(mirrorRow).not.toHaveAccessibleName(/\$\d/)
    const collapse = sidebar().getByRole('button', { name: t('nav.collapseSidebar') })
    expect(collapse).toHaveAttribute('aria-expanded', 'true')
    await user.click(collapse)
    expect(sidebar().getByRole('button', { name: t('nav.expandSidebar') })).toHaveAttribute('aria-expanded', 'false')
  })
})
