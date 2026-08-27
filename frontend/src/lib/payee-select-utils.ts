import { normalizeText } from './utils'
import type { Payee } from '../types'

/** The payees a picker should show, best match first.
 *
 *  Relevance decides the order and a star only breaks ties: a favourite the
 *  user did not search for has no business sitting above the name they typed
 *  the start of. With an empty search every name is a prefix match, so the
 *  ordering degrades to favourites-then-alphabetical on its own.
 *
 *  `limit` exists because sync fills this list with one row per merchant
 *  descriptor. Rendering thousands of items on every open is a visible stall,
 *  and the search box is right there.
 */
export function filterPayees(payees: Payee[], search: string, limit = 200): Payee[] {
  const needle = normalizeText(search.trim())

  const matches: { payee: Payee; rank: number }[] = []
  for (const payee of payees) {
    const name = normalizeText(payee.name)
    if (needle && !name.includes(needle)) continue
    matches.push({ payee, rank: name.startsWith(needle) ? 0 : 1 })
  }

  matches.sort(
    (a, b) =>
      a.rank - b.rank ||
      Number(b.payee.is_favorite) - Number(a.payee.is_favorite) ||
      a.payee.name.localeCompare(b.payee.name),
  )

  return matches.slice(0, limit).map((m) => m.payee)
}
