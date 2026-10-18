import axios from 'axios'
import { connections } from '@/lib/api'
import type { BankConnection } from '@/types'

// A gateway or proxy gave up on the request (no response at all, or a
// 502/503/504) — the backend may still be working on it and finish.
export function isGatewayTimeout(err: unknown): boolean {
  if (!axios.isAxiosError(err)) return false
  if (!err.response) return true
  return [502, 503, 504].includes(err.response.status)
}

// Connecting a bank imports its history inside the callback request, which
// can outlast a proxy's timeout while the import itself succeeds (issue
// #1148). Poll the connection list until a connection for `provider` that
// wasn't in `knownIds` appears; null once `timeoutMs` passes without one.
// The client can't tell which connection its own request created, so when
// more than one new connection for `provider` shows up (another connect ran
// at the same time) it gives up with null rather than claim one of them.
export async function waitForNewConnection(
  provider: string,
  knownIds: ReadonlySet<string>,
  {
    intervalMs = 5000,
    timeoutMs = 180_000,
    list = connections.list,
  }: { intervalMs?: number; timeoutMs?: number; list?: () => Promise<BankConnection[]> } = {},
): Promise<BankConnection | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
    try {
      const added = (await list()).filter((c) => c.provider === provider && !knownIds.has(c.id))
      if (added.length === 1) return added[0]
      if (added.length > 1) return null
    } catch {
      // The list can fail for the same reason the callback did; keep trying.
    }
  }
  return null
}
