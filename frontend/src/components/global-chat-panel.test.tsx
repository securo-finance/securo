import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { GlobalChatPanel } from '@/components/global-chat-panel'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  getDefault: vi.fn(),
  messages: vi.fn(),
  conversations: vi.fn(),
  streamChat: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  agents: {
    list: api.list,
    getDefault: api.getDefault,
    conversations: {
      list: api.conversations,
      messages: api.messages,
      generateTitle: vi.fn().mockResolvedValue({}),
    },
  },
}))

vi.mock('@/lib/agents-stream', () => ({ streamChat: api.streamChat }))

const STORAGE_KEY = 'securo.global-chat'
const agent = { id: 'agent-1', name: 'Helper' }

function stored() {
  return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
}

function httpError(status: number) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status } })
}

describe('GlobalChatPanel stored conversation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ agentId: 'agent-1', conversationByAgent: { 'agent-1': 'conv-gone' } }))
    api.list.mockResolvedValue([agent])
    api.getDefault.mockResolvedValue(agent)
    api.conversations.mockResolvedValue([])
    api.streamChat.mockResolvedValue(undefined)
  })

  it('forgets a conversation that was deleted elsewhere, so the next message starts a new one', async () => {
    api.messages.mockRejectedValue(httpError(404))
    const { user } = renderWithProviders(<GlobalChatPanel open onOpenChange={() => {}} />)

    await waitFor(() => expect(stored().conversationByAgent?.['agent-1']).toBeUndefined())
    // Only the one request: a 404 is not retried.
    expect(api.messages).toHaveBeenCalledTimes(1)

    const input = await screen.findByRole('textbox')
    await user.type(input, 'hello{Enter}')
    await waitFor(() => expect(api.streamChat).toHaveBeenCalled())
    expect(api.streamChat.mock.calls[0][0]).toMatchObject({ agentId: 'agent-1', conversationId: null })
  })

  it('keeps the conversation when loading it fails for another reason', async () => {
    api.messages.mockRejectedValue(httpError(500))
    renderWithProviders(<GlobalChatPanel open onOpenChange={() => {}} />)

    // Retried once, like any other failed load (the app default), then kept.
    await waitFor(() => expect(api.messages).toHaveBeenCalledTimes(2), { timeout: 3000 })
    expect(stored().conversationByAgent?.['agent-1']).toBe('conv-gone')
  })
})
