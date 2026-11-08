/**
 * Cross-tab data-change signal.
 *
 * The query cache is per tab: a change made in another tab (a rule
 * re-applied to existing transactions, an edited transaction) never reaches
 * this tab's cache, so its lists keep showing the stale data until the
 * global staleTime runs out. The response interceptor in `api.ts` bumps this
 * key after every successful mutation, and the browser's `storage` event
 * (which fires in every other tab, never in the one that wrote) is what
 * `useCrossTabRefresh` listens for.
 */
export const DATA_CHANGED_KEY = 'securo.data-changed'

let sequence = 0

/**
 * Record that a mutation succeeded in this tab.
 *
 * A new value on every call so even two quick changes both notify the other
 * tabs: the timestamp alone repeats within the same millisecond, and writing
 * an unchanged value fires no `storage` event. The per-tab counter keeps this
 * tab's writes distinct, the random part keeps two tabs from colliding. No-op where storage is unavailable (private mode): cross-tab refresh
 * is a convenience, and the same-tab invalidations still apply.
 */
export function markDataChanged(): void {
  try {
    sequence += 1
    const value = `${Date.now()}-${sequence}-${Math.random().toString(36).slice(2)}`
    localStorage.setItem(DATA_CHANGED_KEY, value)
  } catch {
    // Ignore: nothing to do when localStorage throws.
  }
}
