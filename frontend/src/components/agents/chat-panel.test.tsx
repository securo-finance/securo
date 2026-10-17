import { describe, expect, it, vi } from 'vitest'
import { screen, fireEvent, waitFor } from '@testing-library/react'

import { ChatPanel } from '@/components/agents/chat-panel'
import { renderWithProviders } from '@/test/utils'
import type { Agent } from '@/lib/api'

const { streamChat } = vi.hoisted(() => ({ streamChat: vi.fn() }))
vi.mock('@/lib/agents-stream', () => ({ streamChat }))

const agent = { id: 'a1', name: 'Assistant', max_history_messages: 20, temperature: 0.4 } as unknown as Agent

function renderPanel() {
  return renderWithProviders(
    <ChatPanel agent={agent} conversationId={null} onConversationCreated={() => {}} />,
  )
}

function type(text: string) {
  const box = screen.getByPlaceholderText(/Assistant/)
  fireEvent.change(box, { target: { value: text } })
  fireEvent.keyDown(box, { key: 'Enter' })
}

describe('ChatPanel', () => {
  it('shows a Stop button while streaming and aborts the request when pressed', async () => {
    let seenSignal: AbortSignal | undefined
    streamChat.mockImplementation((opts: { signal?: AbortSignal }) => {
      seenSignal = opts.signal
      return new Promise<void>((_, reject) => {
        opts.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    })
    renderPanel()
    type('how much did I spend?')
    const stop = await screen.findByRole('button', { name: 'Stop' })
    fireEvent.click(stop)
    await waitFor(() => expect(seenSignal?.aborted).toBe(true))
    // Stopping is not an error: no error banner, Send is back.
    await screen.findByRole('button', { name: 'Send' })
    expect(screen.queryByText(/Something went wrong/)).toBeNull()
  })

  it('translates provider error codes and offers Retry with the same text', async () => {
    streamChat.mockImplementation(async (opts: { onEvent: (ev: unknown) => void }) => {
      opts.onEvent({ kind: 'error', error_code: 'rate_limit', error_message: '429' })
      opts.onEvent({ kind: 'done', finish_reason: 'error' })
    })
    const before = streamChat.mock.calls.length
    renderPanel()
    type('net worth?')
    await screen.findByText(/rate-limiting right now/)
    expect(streamChat.mock.calls.length).toBe(before + 1)
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(streamChat.mock.calls.length).toBe(before + 2))
    expect(streamChat.mock.calls[before + 1][0].content).toBe('net worth?')
  })
})
