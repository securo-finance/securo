import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', () => ({ WORKSPACE_STORAGE_KEY: 'workspace' }))

import { streamChat, type AgentStreamEvent } from './agents-stream'

function respond(status: number, body: string) {
  vi.stubGlobal('localStorage', { getItem: () => null })
  vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status, statusText: 'Forbidden' })))
}

async function events(): Promise<AgentStreamEvent[]> {
  const seen: AgentStreamEvent[] = []
  await streamChat({ agentId: 'a1', content: 'hi', onEvent: (ev) => seen.push(ev) })
  return seen
}

afterEach(() => vi.unstubAllGlobals())

describe('streamChat refused before the stream opens', () => {
  it('turns a quota refusal into an error that names the counter and its maximum', async () => {
    respond(403, JSON.stringify({
      detail: { code: 'quota_exceeded', counter: 'max_assistant_messages_per_month', current: 50, maximum: 50 },
    }))
    const [ev] = await events()
    expect(ev).toMatchObject({
      kind: 'error',
      error_code: 'quota_exceeded',
      quota: { counter: 'max_assistant_messages_per_month', current: 50, maximum: 50 },
    })
  })

  it('keeps the status and the text for any other refusal', async () => {
    respond(404, '{"detail":"agent not found"}')
    const [ev] = await events()
    expect(ev).toEqual({ kind: 'error', error_code: '404', error_message: '{"detail":"agent not found"}' })
  })
})
