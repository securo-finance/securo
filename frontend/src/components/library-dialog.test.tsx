import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { LibraryDialog } from '@/components/library-dialog'
import { renderWithProviders, t } from '@/test/utils'
import type { User } from '@/types'

const state = vi.hoisted(() => ({
  saved: {} as Record<string, { id: string }[] | null>,
  saves: [] as { surface: string; ids: string[] }[],
  user: null as unknown as User,
  pluginsEnabled: true,
}))

const api = vi.hoisted(() => ({
  uiLayouts: {
    get: async () => ({ dashboard: null, reports: null, ...state.saved }),
    save: async (surface: string, ids: string[]) => {
      state.saves.push({ surface, ids })
    },
    reset: async () => {},
  },
  auth: { updateMe: vi.fn() },
  admin: { defaultColors: async () => ({ light: null, dark: null }) },
}))

vi.mock('@/lib/api', () => api)

const updateUser = vi.fn((next: User) => {
  state.user = next
})

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: state.user, updateUser }),
}))

vi.mock('@/hooks/use-feature-flags', () => ({
  useFeatureFlags: () => ({ pluginsEnabled: state.pluginsEnabled, agentsEnabled: false }),
}))

vi.mock('next-themes', () => ({
  useTheme: () => ({ resolvedTheme: 'light' }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  state.saved = {}
  state.saves = []
  state.pluginsEnabled = true
  state.user = {
    id: 'u1',
    email: 'me@example.com',
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_2fa_enabled: false,
    preferences: {},
    theme: null,
  }
  api.auth.updateMe.mockImplementation(async (updates: Partial<User>) => ({ ...state.user, ...updates }))
})

function open(kind: 'widgets' | 'reports' | 'themes') {
  return renderWithProviders(<LibraryDialog kind={kind} onKindChange={() => {}} onClose={() => {}} />)
}

describe('LibraryDialog', () => {
  it('adds a widget left out of the dashboard, at the end', async () => {
    state.saved = { dashboard: [{ id: 'overview' }] }
    const { user } = open('widgets')
    const add = await screen.findByRole('button', { name: t('library.addTo.dashboard', { name: t('goals.dashboardTitle') }) })
    await user.click(add)
    await waitFor(() => expect(state.saves.at(-1)).toEqual({ surface: 'dashboard', ids: ['overview', 'goals'] }))
    // The row flips to remove at once.
    expect(await screen.findByRole('button', { name: t('library.removeFrom.dashboard', { name: t('goals.dashboardTitle') }) })).toBeInTheDocument()
  })

  it('removes a widget from the dashboard', async () => {
    const { user } = open('widgets')
    await user.click(await screen.findByRole('button', { name: t('library.removeFrom.dashboard', { name: t('library.widget.overview') }) }))
    await waitFor(() => expect(state.saves.at(-1)?.ids).not.toContain('overview'))
  })

  it('keeps the last report on the page', async () => {
    state.saved = { reports: [{ id: 'cash_flow' }] }
    open('reports')
    const remove = await screen.findByRole('button', { name: t('library.removeFrom.reports', { name: t('reports.cashFlow') }) })
    await waitFor(() => expect(remove).toBeDisabled())
    expect(remove).toHaveAttribute('title', t('library.lastReport'))
  })

  it('applies a theme and saves it for the person', async () => {
    const { user } = open('themes')
    const themes = screen.getByRole('group', { name: t('library.kind.themes') })
    expect(within(themes).getByRole('button', { name: t('library.theme.securo') })).toHaveAttribute('aria-pressed', 'true')

    await user.click(within(themes).getByRole('button', { name: t('library.theme.ocean') }))
    await waitFor(() => expect(api.auth.updateMe).toHaveBeenCalledWith({ theme: 'ocean' }))
    expect(state.user.theme).toBe('ocean')
  })

  it("saves Securo's own theme as no theme", async () => {
    state.user = { ...state.user, theme: 'forest' }
    const { user } = open('themes')
    await user.click(screen.getByRole('button', { name: t('library.theme.securo') }))
    await waitFor(() => expect(api.auth.updateMe).toHaveBeenCalledWith({ theme: null }))
  })

  it('puts the theme back when saving fails', async () => {
    api.auth.updateMe.mockRejectedValue(new Error('offline'))
    const { user } = open('themes')
    await user.click(screen.getByRole('button', { name: t('library.theme.graphite') }))
    await waitFor(() => expect(state.user.theme).toBeNull())
  })

  it('points to the plugin catalog only when plugins are on', () => {
    open('widgets')
    expect(screen.getByRole('button', { name: t('library.browseCatalog') })).toBeInTheDocument()
  })

  it('has no catalog link when plugins are off', () => {
    state.pluginsEnabled = false
    open('widgets')
    expect(screen.queryByRole('button', { name: t('library.browseCatalog') })).not.toBeInTheDocument()
  })
})
