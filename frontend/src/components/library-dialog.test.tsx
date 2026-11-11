import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { LibraryDialog } from '@/components/library-dialog'
import { renderWithProviders, t } from '@/test/utils'
import type { User } from '@/types'

const state = vi.hoisted(() => ({
  held: null as Promise<void> | null,
  saved: {} as Record<string, { id: string }[] | null>,
  saves: [] as { surface: string; ids: string[] }[],
  user: null as unknown as User,
  pluginsEnabled: true,
}))

const api = vi.hoisted(() => ({
  uiLayouts: {
    get: async () => {
      if (state.held) await state.held
      return { dashboard: null, reports: null, ...state.saved }
    },
    save: async (surface: string, ids: string[]) => {
      state.saves.push({ surface, ids })
    },
    reset: async () => {},
  },
  auth: { updateMe: vi.fn(), me: vi.fn() },
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
  state.held = null
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

  it("can't change anything before the saved arrangement is in", async () => {
    let release: () => void = () => {}
    state.held = new Promise((done) => { release = done })
    state.saved = { dashboard: [{ id: 'overview' }] }
    open('widgets')
    const remove = screen.getByRole('button', { name: t('library.removeFrom.dashboard', { name: t('library.widget.overview') }) })
    expect(remove).toBeDisabled()
    release()
    // Once loaded, the saved arrangement shows, and it can be changed.
    expect(await screen.findByRole('button', { name: t('library.addTo.dashboard', { name: t('goals.dashboardTitle') }) })).toBeEnabled()
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

  it('shows the pick at once, and goes back to what the server has when saving fails', async () => {
    let reject: (reason: Error) => void = () => {}
    api.auth.updateMe.mockReturnValue(new Promise((_, no) => { reject = no }))
    api.auth.me.mockResolvedValue({ ...state.user, theme: 'ocean' })
    const { user } = open('themes')

    await user.click(screen.getByRole('button', { name: t('library.theme.graphite') }))
    expect(updateUser).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'graphite' }))

    reject(new Error('offline'))
    await waitFor(() => expect(updateUser).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'ocean' })))
  })

  it("doesn't let an older save decide the theme after a newer pick", async () => {
    const pending: ((value: User) => void)[] = []
    api.auth.updateMe.mockImplementation(
      (updates: Partial<User>) => new Promise<User>((done) => pending.push(() => done({ ...state.user, ...updates }))),
    )
    const { user } = open('themes')
    await user.click(screen.getByRole('button', { name: t('library.theme.ocean') }))
    await user.click(screen.getByRole('button', { name: t('library.theme.forest') }))

    // Saves go out one after another: the second waits for the first.
    await waitFor(() => expect(pending).toHaveLength(1))
    pending[0](state.user)
    await waitFor(() => expect(pending).toHaveLength(2))
    pending[1](state.user)
    await waitFor(() => expect(api.auth.updateMe).toHaveBeenLastCalledWith({ theme: 'forest' }))
    expect(updateUser).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'forest' }))
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
