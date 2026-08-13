/**
 * Where the active-collection filter (issue #105) actually applies.
 *
 * The "Viewing" bar renders only on these routes, so it never sits above a
 * page it has no effect on. Keep in sync with the pages that read
 * `useCollectionFilter` — a route added here without the page consuming the
 * filter puts the bar back to claiming something untrue.
 */
const COLLECTION_SCOPED_ROUTES = [
  '/', // dashboard
  '/transactions',
  '/accounts',
  '/reports',
  '/assets',
]

/**
 * Whether the active-collection filter scopes the page at `pathname`.
 *
 * Exact matches only: `/accounts` is a filtered list, while `/accounts/:id`
 * shows one account and ignores the filter entirely.
 * @example `appliesCollectionFilter('/accounts') // true`
 */
export function appliesCollectionFilter(pathname: string): boolean {
  // Trailing slashes are equivalent for routing, but '/' must survive.
  const normalized = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  return COLLECTION_SCOPED_ROUTES.includes(normalized)
}
