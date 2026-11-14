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

describe('GlobalChatPanel message from elsewhere', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    api.list.mockResolvedValue([agent])
    api.getDefault.mockResolvedValue(agent)
    api.conversations.mockResolvedValue([])
    api.messages.mockResolvedValue([])
    api.streamChat.mockResolvedValue(undefined)
  })

  it('sends a message typed in the dashboard to the default agent, once', async () => {
    const sent = vi.fn()
    const { rerender } = renderWithProviders(
      <GlobalChatPanel open onOpenChange={() => {}} initialMessage="How am I doing?" onInitialMessageSent={sent} />,
    )
    await waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(1))
    expect(api.streamChat.mock.calls[0][0]).toMatchObject({ agentId: 'agent-1', content: 'How am I doing?' })
    expect(sent).toHaveBeenCalledTimes(1)

    rerender(<GlobalChatPanel open onOpenChange={() => {}} initialMessage="How am I doing?" onInitialMessageSent={sent} />)
    expect(api.streamChat).toHaveBeenCalledTimes(1)

    // Taken (cleared by the parent), then a new question while the panel is still open.
    rerender(<GlobalChatPanel open onOpenChange={() => {}} initialMessage={null} onInitialMessageSent={sent} />)
    rerender(<GlobalChatPanel open onOpenChange={() => {}} initialMessage="And next month?" onInitialMessageSent={sent} />)
    await waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(2))
    expect(api.streamChat.mock.calls[1][0]).toMatchObject({ content: 'And next month?' })
  })
})

describe('GlobalChatPanel message waiting for the default agent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    api.conversations.mockResolvedValue([])
    api.messages.mockResolvedValue([])
    api.streamChat.mockResolvedValue(undefined)
  })

  it('goes to the default agent, not the first one listed, once the default is known', async () => {
    let answer: (value: unknown) => void = () => {}
    api.list.mockResolvedValue([{ id: 'first', name: 'First' }, { id: 'chosen', name: 'Chosen' }])
    api.getDefault.mockReturnValue(new Promise((done) => { answer = done }))
    renderWithProviders(<GlobalChatPanel open onOpenChange={() => {}} initialMessage="Hi" onInitialMessageSent={() => {}} />)

    await new Promise((done) => setTimeout(done, 50))
    expect(api.streamChat).not.toHaveBeenCalled()
    answer({ id: 'chosen', name: 'Chosen' })
    await waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(1))
    expect(api.streamChat.mock.calls[0][0]).toMatchObject({ agentId: 'chosen', content: 'Hi' })
  })

  it('looks up the default when the saved agent no longer exists', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ agentId: 'deleted' }))
    let answer: (value: unknown) => void = () => {}
    api.list.mockResolvedValue([{ id: 'first', name: 'First' }, { id: 'chosen', name: 'Chosen' }])
    api.getDefault.mockReturnValue(new Promise((done) => { answer = done }))
    renderWithProviders(<GlobalChatPanel open onOpenChange={() => {}} initialMessage="Hi" onInitialMessageSent={() => {}} />)

    await waitFor(() => expect(api.getDefault).toHaveBeenCalled())
    expect(api.streamChat).not.toHaveBeenCalled()
    answer({ id: 'chosen', name: 'Chosen' })
    await waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(1))
    expect(api.streamChat.mock.calls[0][0]).toMatchObject({ agentId: 'chosen' })
  })
})
