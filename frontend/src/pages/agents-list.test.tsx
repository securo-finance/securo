import { renderWithProviders } from '@/test/utils'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import AgentsListPage from '@/pages/agents-list'
import { AGENT_TEMPLATES } from '@/lib/agent-templates'
import { AgentFormDialog } from '@/components/agents/agent-form-dialog'

const api = vi.hoisted(() => ({
  info: vi.fn(),
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  setTools: vi.fn(),
  connections: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => ({ agents: api }))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

const CONN_A = { id: 'conn-1', name: '1188 Conn A', is_default: false }
const CONN_B = { id: 'conn-2', name: '1188 Conn B', is_default: true }

function agentStub(id: string, name: string): Record<string, unknown> {
  return {
    id,
    name,
    description: null,
    system_prompt: '',
    icon: 'bot',
    color: '#64748b',
    connection_id: null,
    provider: null,
    model: null,
    temperature: 0.4,
    max_history_messages: 20,
    top_n: 5,
    similarity_threshold: 0.75,
    extra: {},
    auto_context: true,
    is_archived: false,
    is_default: false,
    conversation_count: 0,
    knowledge_count: 0,
    created_at: '2026-10-24T00:00:00Z',
    updated_at: '2026-10-24T00:00:00Z',
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.info.mockResolvedValue({
    enabled: true,
    providers: ['openai'],
    embedding_dim: 512,
    default_top_n: 5,
    default_similarity_threshold: 0.75,
  })
  api.list.mockResolvedValue([])
  api.create.mockResolvedValue(agentStub('agent-new', 'New agent'))
  api.setTools.mockResolvedValue(undefined)
})

describe('agents page templates', () => {
  it('shows every built-in template card', async () => {
    api.connections.list.mockResolvedValue([CONN_A])
    renderWithProviders(<AgentsListPage />)
    expect(await screen.findByText('Savings advisor')).toBeInTheDocument()
    expect(await screen.findByText('Monthly review')).toBeInTheDocument()
    expect(await screen.findByText('Budget watcher')).toBeInTheDocument()
    expect(await screen.findByText('Recurring auditor')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Create' })).toHaveLength(AGENT_TEMPLATES.length)
  })

  it('creates the agent in one click using the default connection and the template tools', async () => {
    api.connections.list.mockResolvedValue([CONN_A, CONN_B])
    const user = userEvent.setup()
    renderWithProviders(<AgentsListPage />)
    await screen.findByText('Savings advisor')
    const createButtons = screen.getAllByRole('button', { name: 'Create' })
    expect(createButtons).toHaveLength(AGENT_TEMPLATES.length)
    await user.click(createButtons[0])
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1))
    const payload = api.create.mock.calls[0][0] as Record<string, unknown>
    expect(payload.name).toBe('Savings advisor')
    expect(payload.connection_id).toBe('conn-2')
    expect(payload.system_prompt).toContain("savings advisor")
    expect(payload.auto_context).toBe(true)
    // The tool scope is part of the create call, never a second request
    // that could fail and leave the agent with every tool allowed.
    const items = payload.tools as { server: string; tool_name: string; enabled: boolean }[]
    expect(items).toContainEqual({ server: 'securo', tool_name: 'propose_create_budget', enabled: true })
    expect(items).toHaveLength(AGENT_TEMPLATES[0].tools.length)
    expect(api.setTools).not.toHaveBeenCalled()
  })

  it('leaves no agent behind when the one-click creation fails', async () => {
    api.connections.list.mockResolvedValue([CONN_A])
    api.create.mockRejectedValue(new Error('boom'))
    const user = userEvent.setup()
    renderWithProviders(<AgentsListPage />)
    await screen.findByText('Savings advisor')
    await user.click(screen.getAllByRole('button', { name: 'Create' })[0])
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1))
    // A single request carries the agent and its tools: when it fails there
    // is nothing to follow up with, and no separate tool call is attempted.
    expect(api.setTools).not.toHaveBeenCalled()
    expect(api.list).toHaveBeenCalledTimes(1)
  })

  it('opens the form prefilled when no LLM connection exists yet', async () => {
    api.connections.list.mockResolvedValue([])
    const user = userEvent.setup()
    renderWithProviders(<AgentsListPage />)
    await screen.findByText('Savings advisor')
    await user.click(screen.getAllByRole('button', { name: 'Create' })[0])
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByDisplayValue('Savings advisor')).toBeInTheDocument()
    // testing-library normalizes the element value but not the expected
    // string, so collapse the prompt's whitespace to match.
    const promptNormalized = AGENT_TEMPLATES[0].systemPrompt.replace(/\s+/g, ' ').trim()
    expect(await within(dialog).findByDisplayValue(promptNormalized)).toBeInTheDocument()
    expect(api.create).not.toHaveBeenCalled()
  })

  it('applies the template tools when the agent is created from the prefilled form', async () => {
    api.connections.list.mockResolvedValue([CONN_A])
    const user = userEvent.setup()
    renderWithProviders(
      <AgentFormDialog open onOpenChange={() => {}} providers={['openai']} preset={AGENT_TEMPLATES[2]} />,
    )
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByDisplayValue('Budget watcher')).toBeInTheDocument()
    const submit = within(dialog).getByRole('button', { name: 'Create' })
    await waitFor(() => expect(submit).toBeEnabled())
    await user.click(submit)
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1))
    const payload = api.create.mock.calls[0][0] as { tools: { tool_name: string }[] }
    expect(payload.tools.map((i) => i.tool_name)).toEqual(AGENT_TEMPLATES[2].tools)
    expect(api.setTools).not.toHaveBeenCalled()
  })
})
