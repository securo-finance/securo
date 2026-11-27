import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import { PluginConnections } from '@/components/plugin-connections'
import { PluginPlanDialog } from '@/components/plugin-plan-dialog'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { renderWithProviders, t } from '@/test/utils'
import type { InstalledPlugin, PluginConnection, PluginPlan } from '@/types'

const api = vi.hoisted(() => ({
  plugins: {
    plan: vi.fn(),
    apply: vi.fn(),
    connections: vi.fn(),
    connect: vi.fn(),
    sync: vi.fn(),
    authorize: vi.fn(),
    disconnect: vi.fn(),
    runRoutine: vi.fn(),
  },
}))

vi.mock('@/lib/api', () => api)

const auth = vi.hoisted(() => ({ user: { theme: null as string | null }, updateUser: vi.fn() }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => auth }))

const PLAN: PluginPlan = {
  action: 'install',
  plugin_id: 'acme/ledger',
  version: '1.0.0',
  commit: '1'.repeat(40),
  token: 't'.repeat(64),
  create: [],
  update: [],
  delete: [],
  keep: [],
  release: [],
  existing: [],
  unresolved: [],
  default_categories: [],
  code: {
    network: [{ host: 'api.ledger.example', purpose: { en: 'Reads your payouts' } }],
    api_scopes: [{ scope: 'transactions:write', purpose: { en: 'Adds your payouts' } }],
    integrations: [{ key: 'keys', name: { en: 'Ledger' }, auth: 'credentials' }],
    new_hosts: [],
    new_scopes: ['transactions:write'],
    connections: 0,
  },
}

const PLUGIN: InstalledPlugin = {
  plugin_id: 'acme/ledger',
  name: { en: 'Ledger' },
  version: '1.0.0',
  commit: '1'.repeat(40),
  installed_at: '2026-11-12T00:00:00Z',
  integrations: [
    {
      key: 'keys',
      name: { en: 'Ledger (API key)' },
      auth: 'credentials',
      fields: [
        { key: 'api_key', label: { en: 'API key' }, secret: true, required: true },
        { key: 'account', label: { en: 'Account' }, secret: false, required: false },
      ],
      sync_minutes: 60,
    },
  ],
  network: [],
  api_scopes: [],
  redirect_uri: null,
}

const CONNECTION: PluginConnection = {
  id: 'c1',
  plugin_id: 'acme/ledger',
  integration: 'keys',
  name: 'Main',
  status: 'error',
  config: {},
  secret_fields: ['api_key'],
  sync_minutes: 60,
  last_sync_at: null,
  last_error: 'RuntimeError: the service is down',
  created_at: '2026-11-12T00:00:00Z',
}

function workspace(canManage: boolean, canWrite = true) {
  return { current: { id: 'ws' }, canManage, canWrite, hasModule: () => false } as unknown as NonNullable<
    React.ContextType<typeof WorkspaceContext>
  >
}

beforeEach(() => {
  Object.values(api.plugins).forEach((fn) => fn.mockReset())
})

describe('PluginPlanDialog with code', () => {
  it('shows the hosts and the access the owner agrees to', async () => {
    api.plugins.plan.mockResolvedValue(PLAN)
    api.plugins.apply.mockResolvedValue(PLAN)
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginPlanDialog target={{ id: 'acme/ledger', name: 'Ledger', action: 'install' }} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText('api.ledger.example')).toBeInTheDocument()
    expect(screen.getByText('Reads your payouts')).toBeInTheDocument()
    expect(screen.getByText(t('plugins.code.write', { what: t('plugins.code.resource.transactions') }))).toBeInTheDocument()
    expect(screen.getByText(t('plugins.code.new'))).toBeInTheDocument()
    expect(screen.getByText(t('plugins.code.runsCode'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('plugins.confirm.install') }))
    await waitFor(() => expect(api.plugins.apply).toHaveBeenCalled())
  })

  it("can't be confirmed by someone who isn't the owner", async () => {
    api.plugins.plan.mockResolvedValue(PLAN)
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(false)}>
        <PluginPlanDialog target={{ id: 'acme/ledger', name: 'Ledger', action: 'install' }} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText(t('plugins.code.ownerOnly'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('plugins.confirm.install') })).toBeDisabled()
  })

  it('says nothing leaves the server when no host is declared', async () => {
    api.plugins.plan.mockResolvedValue({ ...PLAN, code: { ...PLAN.code!, network: [] } })
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginPlanDialog target={{ id: 'acme/ledger', name: 'Ledger', action: 'install' }} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText(t('plugins.code.noNetwork'))).toBeInTheDocument()
    expect(screen.queryByText(t('plugins.code.provides'))).not.toBeInTheDocument()
  })

  it('lists the records an integration syncs as a connected bank', async () => {
    const integrations = [
      { key: 'keys', name: { en: 'Ledger' }, auth: 'credentials' as const, provides: ['accounts', 'holdings'] as const },
      { key: 'hooks', name: { en: 'Ledger hooks' }, auth: 'none' as const, provides: null },
    ].map((i) => ({ ...i, provides: i.provides ? [...i.provides] : null }))
    api.plugins.plan.mockResolvedValue({ ...PLAN, code: { ...PLAN.code!, integrations } })
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginPlanDialog target={{ id: 'acme/ledger', name: 'Ledger', action: 'install' }} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText(t('plugins.code.provides'))).toBeInTheDocument()
    expect(screen.getByText(`${t('plugins.code.kinds.accounts')}, ${t('plugins.code.kinds.holdings')}`)).toBeInTheDocument()
    expect(screen.queryByText('Ledger hooks')).not.toBeInTheDocument()
  })
})

describe('PluginPlanDialog with themes', () => {
  it('warns whoever uses a theme that leaves the library', async () => {
    auth.user = { theme: 'plugin:acme/ledger:matrix' }
    api.plugins.plan.mockResolvedValue({
      ...PLAN, action: 'remove', commit: null, code: null,
      themes: { added: [], removed: [{ key: 'matrix', name: { en: 'Matrix' } }] },
    })
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginPlanDialog target={{ id: 'acme/ledger', name: 'Ledger', action: 'remove' }} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText(t('plugins.plan.themesRemove', { names: 'Matrix' }))).toBeInTheDocument()
    expect(screen.getByText(t('plugins.plan.themeInUse', { name: 'Matrix' }))).toBeInTheDocument()
    auth.user = { theme: null }
  })
})

describe('PluginConnections', () => {
  it('lists connections with their status and error', async () => {
    api.plugins.connections.mockResolvedValue([CONNECTION])
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginConnections plugin={PLUGIN} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText('Main')).toBeInTheDocument()
    expect(screen.getByText(t('plugins.connections.status.error'))).toBeInTheDocument()
    expect(screen.getByText('RuntimeError: the service is down')).toBeInTheDocument()
  })

  it('adds a connection from the integration form, secrets as passwords', async () => {
    const created = { ...CONNECTION, id: 'c2', name: 'Ledger (API key)', status: 'pending' as const, last_error: null }
    // Like the server: once created, the connection is in the list.
    let saved: PluginConnection[] = []
    api.plugins.connections.mockImplementation(async () => saved)
    api.plugins.connect.mockImplementation(async () => {
      saved = [created]
      return { connection: created, authorize_url: null }
    })
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginConnections plugin={PLUGIN} />
      </WorkspaceContext.Provider>,
    )
    await user.click(await screen.findByRole('button', { name: t('plugins.connections.add') }))
    const connect = screen.getByRole('button', { name: t('plugins.connections.connect') })
    expect(connect).toBeDisabled() // the required key is missing
    const key = screen.getByLabelText('API key')
    expect(key).toHaveAttribute('type', 'password')
    await user.type(key, 'sk_live_1')
    await user.click(connect)
    await waitFor(() =>
      expect(api.plugins.connect).toHaveBeenCalledWith({
        plugin_id: 'acme/ledger', integration: 'keys', name: '', values: { api_key: 'sk_live_1' },
      }),
    )
    // The dialog closes and the new connection shows, waiting for its first sync.
    await waitFor(() => expect(screen.queryByLabelText('API key')).not.toBeInTheDocument())
    expect(await screen.findByText(t('plugins.connections.status.pending'))).toBeInTheDocument()
    expect(screen.getAllByText('Ledger (API key)').length).toBeGreaterThan(0)
  })

  it('tells an update that drops the code what goes with it', async () => {
    api.plugins.plan.mockResolvedValue({
      ...PLAN, action: 'update', code: { ...PLAN.code!, connections: 2, drops_code: true },
    })
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginPlanDialog target={{ id: 'acme/ledger', name: 'Ledger', action: 'update' }} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText(t('plugins.code.removeConnections', { count: 2 }))).toBeInTheDocument()
    expect(screen.queryByText('api.ledger.example')).not.toBeInTheDocument()
  })

  it('says the accounts of the banks a removal ends stay, as manual ones', async () => {
    api.plugins.plan.mockResolvedValue({
      ...PLAN, action: 'remove', commit: null, code: { ...PLAN.code!, connections: 1, unlinks: 1 },
    })
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginPlanDialog target={{ id: 'acme/ledger', name: 'Ledger', action: 'remove' }} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    const unlinks = t('plugins.code.unlinks', { count: 1 })
    expect(await screen.findByText((_, el) => el?.tagName === 'SPAN' && !!el.textContent?.includes(unlinks))).toBeInTheDocument()
  })

  it('warns when an update stops syncing a bank', async () => {
    api.plugins.plan.mockResolvedValue({ ...PLAN, action: 'update', code: { ...PLAN.code!, unlinks: 2 } })
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginPlanDialog target={{ id: 'acme/ledger', name: 'Ledger', action: 'update' }} onClose={() => {}} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText(t('plugins.code.unlinks', { count: 2 }))).toBeInTheDocument()
  })

  it('says removing a connection that syncs as a bank removes its accounts', async () => {
    api.plugins.connections.mockResolvedValue([CONNECTION])
    const bank = { ...PLUGIN, integrations: PLUGIN.integrations.map((i) => ({ ...i, provides: ['accounts' as const] })) }
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginConnections plugin={bank} />
      </WorkspaceContext.Provider>,
    )
    await user.click(await screen.findByRole('button', { name: t('plugins.connections.removeAria', { name: 'Main' }) }))
    expect(screen.getByText(t('plugins.connections.removeBankHint'))).toBeInTheDocument()
    expect(screen.queryByText(t('plugins.connections.removeHint'))).not.toBeInTheDocument()
  })

  it('hides the actions from viewers', async () => {
    api.plugins.connections.mockResolvedValue([CONNECTION])
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(false, false)}>
        <PluginConnections plugin={PLUGIN} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText('Main')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('plugins.connections.add') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('plugins.connections.syncNow') })).not.toBeInTheDocument()
  })
})

const FILES: InstalledPlugin = {
  ...PLUGIN,
  integrations: [
    {
      key: 'files',
      name: { en: 'Files' },
      auth: 'credentials',
      fields: [
        { key: 'bundle', label: { en: 'Key bundle' }, secret: true, required: true, type: 'file', accept: ['.p12'], max_kb: 1 },
        { key: 'layout', label: { en: 'Layout' }, secret: false, required: false, type: 'file' },
      ],
      sync_minutes: null,
      routines: [
        { key: 'send', name: { en: 'Send' }, every_minutes: 15 },
        { key: 'collect', name: { en: 'Collect' }, every_minutes: 1440 },
      ],
    },
  ],
}

const WITH_ROUTINES: PluginConnection = {
  ...CONNECTION,
  integration: 'files',
  status: 'active',
  last_error: null,
  sync_minutes: null,
  config: { layout: { name: 'layout.csv', size: 8 } },
  secret_fields: ['bundle'],
  routines: [
    { key: 'send', every_minutes: 15, last_run_at: null, last_error: null, failures: 0 },
    { key: 'collect', every_minutes: 1440, last_run_at: null, last_error: 'RuntimeError: refused', failures: 2 },
  ],
}

describe('PluginConnections: files and routines', () => {
  it('sends a picked file as its name and base64 content, and refuses one of another type or too large', async () => {
    api.plugins.connections.mockResolvedValue([])
    api.plugins.connect.mockResolvedValue({ connection: { ...WITH_ROUTINES, id: 'c3' }, authorize_url: null })
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginConnections plugin={FILES} />
      </WorkspaceContext.Provider>,
    )
    await user.click(await screen.findByRole('button', { name: t('plugins.connections.add') }))
    const input = screen.getByLabelText('Key bundle')
    expect(input).toHaveAttribute('type', 'file')
    expect(input).toHaveAttribute('accept', '.p12')
    expect(screen.getByText(t('plugins.connections.fileAccepts', { types: '.p12', size: 1 }))).toBeInTheDocument()
    const connect = screen.getByRole('button', { name: t('plugins.connections.connect') })

    fireEvent.change(input, { target: { files: [new File(['x'], 'notes.txt')] } })
    expect(connect).toBeDisabled()
    fireEvent.change(input, { target: { files: [new File(['x'.repeat(2048)], 'big.p12')] } })
    expect(connect).toBeDisabled()

    fireEvent.change(input, { target: { files: [new File(['hello'], 'keys.p12')] } })
    await waitFor(() => expect(connect).toBeEnabled())
    // A refused replacement drops the file picked before it.
    fireEvent.change(input, { target: { files: [new File(['x'], 'other.txt')] } })
    await waitFor(() => expect(connect).toBeDisabled())
    fireEvent.change(input, { target: { files: [new File(['hello'], 'keys.p12')] } })
    await waitFor(() => expect(connect).toBeEnabled())
    await user.click(connect)
    await waitFor(() =>
      expect(api.plugins.connect).toHaveBeenCalledWith({
        plugin_id: 'acme/ledger', integration: 'files', name: '', values: { bundle: { name: 'keys.p12', content: btoa('hello') } },
      }),
    )
  })

  it("says which file a connection keeps, without asking for it again", async () => {
    api.plugins.connections.mockResolvedValue([])
    const { PluginConnectionDialog } = await import('@/components/plugin-connections')
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginConnectionDialog plugin={FILES} connection={WITH_ROUTINES} onClose={() => {}} onDone={() => {}} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText(/layout\.csv/)).toBeInTheDocument()
    expect(screen.getByText(new RegExp(t('plugins.connections.fileSaved')))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('common.save') })).toBeEnabled()
  })

  it('lists each routine with its schedule and error, and runs one now', async () => {
    const ran: PluginConnection = {
      ...WITH_ROUTINES,
      routines: [{ ...WITH_ROUTINES.routines![0], last_run_at: new Date().toISOString() }, WITH_ROUTINES.routines![1]],
    }
    // Like the server: once it ran, the list reads it as run.
    let current = WITH_ROUTINES
    api.plugins.connections.mockImplementation(async () => [current])
    api.plugins.runRoutine.mockImplementation(async () => {
      current = ran
      return ran
    })
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginConnections plugin={FILES} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText('Send')).toBeInTheDocument()
    expect(screen.getByText('Collect')).toBeInTheDocument()
    expect(screen.getByText('RuntimeError: refused')).toBeInTheDocument()
    expect(screen.getAllByText(new RegExp(t('plugins.connections.routineNeverRan'))).length).toBe(2)
    // It only runs routines: nothing to sync.
    expect(screen.queryByRole('button', { name: t('plugins.connections.syncNow') })).not.toBeInTheDocument()
    expect(screen.queryByText(new RegExp(t('plugins.connections.neverSynced')))).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('plugins.connections.runRoutineAria', { name: 'Send' }) }))
    await waitFor(() => expect(api.plugins.runRoutine).toHaveBeenCalledWith(
      { connectionId: 'c1', routine: 'send' }, expect.anything(),
    ))
    // Send now shows it ran; Collect still hasn't.
    await waitFor(() => expect(screen.getAllByText(new RegExp(t('plugins.connections.routineNeverRan'))).length).toBe(1))
  })

  it('offers to update the keys of a connection whose key was refused', async () => {
    const refused: PluginConnection = {
      ...WITH_ROUTINES,
      status: 'needs_auth',
      last_error: 'AuthError: the key was refused',
    }
    api.plugins.connections.mockResolvedValue([refused])
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={workspace(true)}>
        <PluginConnections plugin={FILES} />
      </WorkspaceContext.Provider>,
    )
    // Nothing runs until the keys are fixed, but the form that set it up is there.
    expect(await screen.findByText(t('plugins.connections.status.keysRefused'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('plugins.connections.runRoutineAria', { name: 'Send' }) })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('plugins.connections.updateKeys') }))
    expect(screen.getByText(t('plugins.connections.editTitle', { name: 'Main' }))).toBeInTheDocument()
    // A field already set can stay blank; it keeps its value.
    expect(screen.getByLabelText('Key bundle')).toHaveAttribute('type', 'file')
  })

  it("doesn't offer to run a routine to viewers", async () => {
    api.plugins.connections.mockResolvedValue([WITH_ROUTINES])
    renderWithProviders(
      <WorkspaceContext.Provider value={workspace(false, false)}>
        <PluginConnections plugin={FILES} />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText('Send')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('plugins.connections.runRoutineAria', { name: 'Send' }) })).not.toBeInTheDocument()
  })
})
