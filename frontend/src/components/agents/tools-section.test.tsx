import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithProviders } from '@/test/utils'

const { tools, setTools } = vi.hoisted(() => ({ tools: vi.fn(), setTools: vi.fn() }))
vi.mock('@/lib/api', () => ({ agents: { tools, setTools } }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: true }) }))

import { ToolsSection } from './tools-section'

const handle = (name: string, enabled: boolean) => ({
  server: 'securo',
  name,
  description: '',
  is_proposal: false,
  enabled,
})

describe('ToolsSection', () => {
  beforeEach(() => {
    tools.mockReset()
    setTools.mockReset().mockResolvedValue(undefined)
  })

  it('shows how many tools are enabled and enables the rest in one click', async () => {
    tools.mockResolvedValue({
      servers: [{ name: 'securo' }],
      tools: [handle('list_accounts', true), handle('list_budgets', false), handle('delete_rule', false)],
    })
    renderWithProviders(<ToolsSection agentId="a1" />)

    expect(await screen.findByTestId('tools-enabled-count')).toHaveTextContent('1 of 3 enabled')

    fireEvent.click(screen.getByRole('button', { name: 'Enable all' }))
    expect(screen.getByTestId('tools-enabled-count')).toHaveTextContent('3 of 3 enabled')
    expect(screen.queryByRole('button', { name: 'Enable all' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(setTools).toHaveBeenCalled())
    expect(setTools.mock.calls[0][1]).toEqual([
      { server: 'securo', tool_name: 'list_accounts', enabled: true },
      { server: 'securo', tool_name: 'list_budgets', enabled: true },
      { server: 'securo', tool_name: 'delete_rule', enabled: true },
    ])
  })

  it('hides the count when no tools were discovered', async () => {
    tools.mockResolvedValue({ servers: [], tools: [] })
    renderWithProviders(<ToolsSection agentId="a1" />)

    expect(await screen.findByText(/No tools discovered/)).toBeInTheDocument()
    expect(screen.queryByTestId('tools-enabled-count')).not.toBeInTheDocument()
  })
})
