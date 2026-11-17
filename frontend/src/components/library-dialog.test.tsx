import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { LibraryDialog } from '@/components/library-dialog'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { renderWithProviders, t } from '@/test/utils'
import type { User } from '@/types'

const state = vi.hoisted(() => ({
  held: null as Promise<void> | null,
  workspaces: [] as (string | null | undefined)[],
  saved: {} as Record<string, { id: string }[] | null>,
  saves: [] as { surface: string; ids: string[]; items: { id: string; span?: number }[] }[],
  user: null as unknown as User,
  pluginsEnabled: true,
  installed: [] as unknown[],
  mode: 'light' as 'light' | 'dark',
  listeners: new Set<() => void>(),
}))

const api = vi.hoisted(() => ({
  uiLayouts: {
    get: async (workspaceId?: string | null) => {
      state.workspaces.push(workspaceId)
      if (state.held) await state.held
      return { dashboard: null, reports: null, ...state.saved }
    },
    // Like the server: what was saved is what the next read returns.
    save: async (surface: string, items: { id: string; span?: number }[], workspaceId?: string | null) => {
      state.saves.push({ surface, ids: items.map((item) => item.id), items })
      state.workspaces.push(workspaceId)
      state.saved = { ...state.saved, [surface]: items }
    },
    reset: async (surface: string) => {
      state.saved = { ...state.saved, [surface]: null }
    },
  },
  auth: { updateMe: vi.fn(), me: vi.fn() },
  admin: { defaultColors: async () => ({ light: null, dark: null }) },
  plugins: { installed: async () => state.installed },
}))

vi.mock('@/lib/api', () => api)

// A reactive signed-in user, so the dialog re-renders with what was saved.
const updateUser = vi.fn((next: User) => {
  state.user = next
  state.listeners.forEach((notify) => notify())
})

vi.mock('@/contexts/auth-context', async () => {
  const { useSyncExternalStore } = await import('react')
  const subscribe = (notify: () => void) => {
    state.listeners.add(notify)
    return () => state.listeners.delete(notify)
  }
  return {
    useAuth: () => ({ user: useSyncExternalStore(subscribe, () => state.user), updateUser }),
  }
})

vi.mock('@/hooks/use-feature-flags', () => ({
  useFeatureFlags: () => ({ pluginsEnabled: state.pluginsEnabled, agentsEnabled: false }),
}))

vi.mock('next-themes', () => ({
  useTheme: () => ({ resolvedTheme: state.mode }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  state.saved = {}
  state.saves = []
  state.held = null
  state.workspaces = []
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
    await waitFor(() => expect(state.saves.at(-1)?.items).toEqual([{ id: 'overview' }, { id: 'goals' }]))
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

  it('reads and saves the arrangement of the workspace it was opened in', async () => {
    const workspace = { current: { id: 'ws-a' }, hasModule: () => false } as unknown as NonNullable<React.ContextType<typeof WorkspaceContext>>
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={workspace}>
        <LibraryDialog kind="widgets" onKindChange={() => {}} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    await user.click(await screen.findByRole('button', { name: t('library.removeFrom.dashboard', { name: t('library.widget.overview') }) }))
    await waitFor(() => expect(state.saves).toHaveLength(1))
    // Every read and the write named ws-a, whatever the active workspace is when they go out.
    expect(new Set(state.workspaces)).toEqual(new Set(['ws-a']))
  })

  it("lists what this workspace can't use, unavailable, and says why", async () => {
    open('widgets')
    const name = t('widgets.receivables.title')
    expect(await screen.findByRole('button', { name: t('library.addTo.dashboard', { name }) })).toBeDisabled()
    const row = screen.getByText(name).closest('li')!
    expect(row).toHaveAttribute('aria-disabled', 'true')
    expect(within(row).getByText(t('library.needs.invoices'))).toBeInTheDocument()
    // The agent widget too, with agents off.
    expect(screen.getByRole('button', { name: t('library.addTo.dashboard', { name: t('widgets.assistant.title') }) })).toBeDisabled()
    expect(screen.getByText(t('library.needs.assistant'))).toBeInTheDocument()
  })

  it('offers the invoice widgets in a workspace that shows invoices', async () => {
    state.saved = { dashboard: [{ id: 'overview' }] }
    const workspace = { current: { id: 'ws-b' }, hasModule: (id: string) => id === 'invoices' } as unknown as NonNullable<React.ContextType<typeof WorkspaceContext>>
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={workspace}>
        <LibraryDialog kind="widgets" onKindChange={() => {}} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    const name = t('widgets.receivables.title')
    expect(screen.getByText(name).closest('li')).not.toHaveAttribute('aria-disabled')
    await user.click(await screen.findByRole('button', { name: t('library.addTo.dashboard', { name }) }))
    await waitFor(() => expect(state.saves.at(-1)?.ids).toEqual(['overview', 'receivables']))
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

  it("lists an installed plugin's themes and saves the pick by its id", async () => {
    state.installed = [
      {
        plugin_id: 'securo-finance/plugin-neon',
        name: { en: 'Neon themes' },
        version: '1.0.0',
        commit: 'a'.repeat(40),
        installed_at: '2026-11-14T00:00:00Z',
        integrations: [],
        network: [],
        api_scopes: [],
        redirect_uri: null,
        themes: [
          {
            key: 'synthwave',
            name: { en: 'Synthwave' },
            light: {},
            // A value that isn't a plain color never reaches the page.
            dark: { background: '#0D0221', primary: '#FF2BD6', border: 'url(https://evil.example/x)' },
            radius: 0,
            font: 'mono',
          },
        ],
      },
    ]
    const { user } = open('themes')
    const pick = await screen.findByRole('button', { name: /Synthwave/ })
    expect(within(pick).getByText(t('library.fromPlugin', { name: 'Neon themes' }))).toBeInTheDocument()
    await user.click(pick)
    await waitFor(() =>
      expect(api.auth.updateMe).toHaveBeenCalledWith({ theme: 'plugin:securo-finance/plugin-neon:synthwave' }),
    )
    await waitFor(() => expect(document.documentElement.style.getPropertyValue('--app-font')).toContain('monospace'))
    state.installed = []
  })

  it("applies a plugin theme's dark colors, never a value that isn't a color", async () => {
    state.mode = 'dark'
    state.user = { ...state.user, theme: 'plugin:securo-finance/plugin-neon:synthwave' }
    state.installed = [
      {
        plugin_id: 'securo-finance/plugin-neon',
        name: { en: 'Neon themes' },
        version: '1.0.0',
        commit: 'a'.repeat(40),
        installed_at: '2026-11-14T00:00:00Z',
        integrations: [],
        network: [],
        api_scopes: [],
        redirect_uri: null,
        themes: [
          {
            key: 'synthwave',
            name: { en: 'Synthwave' },
            light: {},
            dark: { background: '#0D0221', border: 'url(https://evil.example/x)' },
          },
        ],
      },
    ]
    open('themes')
    const root = document.documentElement.style
    await waitFor(() => expect(root.getPropertyValue('--background')).toBe('#0D0221'))
    expect(root.getPropertyValue('--border')).toBe('')
    state.installed = []
    state.mode = 'light'
    state.user = { ...state.user, theme: null }
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

    expect(screen.getByRole('button', { name: t('library.theme.graphite') })).toHaveAttribute('aria-pressed', 'true')

    reject(new Error('offline'))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: t('library.theme.ocean') })).toHaveAttribute('aria-pressed', 'true'),
    )
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
    // The older save is done, and the newer pick still shows.
    expect(screen.getByRole('button', { name: t('library.theme.forest') })).toHaveAttribute('aria-pressed', 'true')
    pending[1](state.user)
    await waitFor(() => expect(api.auth.updateMe).toHaveBeenLastCalledWith({ theme: 'forest' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: t('library.theme.forest') })).toHaveAttribute('aria-pressed', 'true'),
    )
    expect(screen.getByRole('button', { name: t('library.theme.ocean') })).toHaveAttribute('aria-pressed', 'false')
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
