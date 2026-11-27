/**
 * Tiny SSE consumer for /api/agents/{id}/chat.
 *
 * We can't use the native EventSource because it doesn't allow setting an
 * Authorization header. Instead we POST with fetch, then parse the SSE
 * response stream by hand.
 */
import { WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { readQuotaRefusalBody } from '@/lib/quota'
import type { QuotaUsage } from '@/types'

export type AgentStreamEvent =
  | { kind: 'conversation'; conversation_id: string }
  | { kind: 'text_delta'; text: string }
  | { kind: 'tool_call'; tool_name: string; tool_args: Record<string, unknown> }
  | { kind: 'tool_result'; tool_name: string; tool_result: { ok?: boolean; data?: unknown; text?: string | null } }
  // `quota`: the chat was refused because a usage limit filled (ADR 0053).
  | { kind: 'error'; error_code?: string; error_message?: string; quota?: QuotaUsage }
  | { kind: 'done'; finish_reason?: string }

export interface SendMessageOptions {
  agentId: string
  content: string
  conversationId?: string | null
  channel?: string
  // Snapshot of the page the user was on when sending. Forwarded as a
  // system primer so the agent can answer "what about this row?" style
  // questions. Built by the global slide-over from page registrations.
  pageContext?: Record<string, unknown> | null
  signal?: AbortSignal
  onEvent: (ev: AgentStreamEvent) => void
}

export async function streamChat(opts: SendMessageOptions): Promise<void> {
  const token = localStorage.getItem('token') || ''
  // SSE uses raw fetch, so we have to set the workspace header here
  // — the axios interceptor that adds it for the rest of the app
  // doesn't run on this code path.
  const workspaceId = localStorage.getItem(WORKSPACE_STORAGE_KEY) || ''
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Authorization: token ? `Bearer ${token}` : '',
  }
  if (workspaceId) headers['X-Workspace-Id'] = workspaceId
  const res = await fetch(`/api/agents/${opts.agentId}/chat`, {
    method: 'POST',
    signal: opts.signal,
    headers,
    body: JSON.stringify({
      content: opts.content,
      conversation_id: opts.conversationId ?? null,
      channel: opts.channel ?? 'web',
      page_context: opts.pageContext ?? null,
    }),
  })

  if (!res.ok || !res.body) {
    let detail = ''
    try {
      detail = await res.text()
    } catch {
      detail = ''
    }
    const quota = readQuotaRefusalBody(detail)
    opts.onEvent(quota
      ? { kind: 'error', error_code: 'quota_exceeded', error_message: detail, quota }
      : { kind: 'error', error_code: String(res.status), error_message: detail || res.statusText })
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  // SSE framing: events are separated by a blank line. Within an event,
  // `event: <type>` and `data: <json>` lines. We accumulate lines into a
  // buffer until we hit a blank line, then dispatch.
  let currentEvent = 'message'
  let currentData = ''

  function flushEvent() {
    if (!currentData) {
      currentEvent = 'message'
      return
    }
    try {
      const parsed = JSON.parse(currentData)
      const ev = { kind: currentEvent, ...parsed } as AgentStreamEvent
      opts.onEvent(ev)
    } catch {
      // Malformed payload — surface as error so the UI can recover.
      opts.onEvent({ kind: 'error', error_code: 'parse', error_message: currentData })
    }
    currentEvent = 'message'
    currentData = ''
  }

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, idx).replace(/\r$/, '')
      buffer = buffer.slice(idx + 1)
      if (line === '') {
        flushEvent()
      } else if (line.startsWith('event:')) {
        currentEvent = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        currentData += (currentData ? '\n' : '') + line.slice(5).trim()
      }
    }
  }
  flushEvent()
}
