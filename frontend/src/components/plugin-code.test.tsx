import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

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
