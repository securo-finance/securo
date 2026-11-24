/** First wait before asking the server again after it could not be reached. */
export const RECONNECT_FIRST_DELAY_MS = 2_000
/** Longest wait between two automatic attempts. */
export const RECONNECT_MAX_DELAY_MS = 30_000

/**
 * How long to wait before the next automatic reconnect, after `failures`
 * failed attempts in a row: 2 s, then doubling, capped at 30 s. The offline
 * screen promises to reconnect on its own, and a server that restarts while
 * the browser stays online fires no browser event to retry on (issue #292).
 */
export function reconnectDelay(failures: number): number {
  const doublings = Math.max(0, failures - 1)
  return Math.min(RECONNECT_FIRST_DELAY_MS * 2 ** doublings, RECONNECT_MAX_DELAY_MS)
}
