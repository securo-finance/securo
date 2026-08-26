/**
 * Which country a stored fiscal document belongs to.
 *
 * Needed because a document's *name* is not always enough to place it. "VAT
 * number" is the label nineteen jurisdictions share, so seeing it on a saved
 * contact says nothing about which country's number it is. Every other
 * document is asked for by exactly one country and needs no help.
 *
 * Two sources, in order:
 *
 *   1. **The packs.** A kind that only one jurisdiction lists can only be that
 *      country's document. This covers forty of the forty-two kinds. The map
 *      comes from the server with the kinds themselves, so it cannot disagree
 *      with what the picker offers.
 *   2. **The value.** For `vat`, the country is *inside the number* — that is
 *      precisely why one kind can serve nineteen countries. `NL802225395B01`
 *      is Dutch because it says so.
 *
 * Returns null rather than guessing. `other` never resolves: it means the
 * product does not know what the document is, and inferring a country from the
 * first two characters of an unknown string would be a confident-looking lie.
 */
import { countryFlag } from './country-flag'
import { isKnownRegion } from './country-name'

/** VAT prefixes that are not the country's own ISO code. */
const VAT_PREFIX_ALIASES: Record<string, string> = {
  // Greece bills under EL, from Ελλάς, which predates the ISO code.
  EL: 'GR',
  // Northern Ireland, which kept an EU-facing prefix after Brexit.
  XI: 'GB',
}

export interface JurisdictionKinds {
  code: string
  kinds: string[]
}

export function taxIdCountry(
  kind: string,
  value: string,
  jurisdictions: JurisdictionKinds[],
): string | null {
  if (!kind || kind === 'other') return null

  const owners = jurisdictions.filter((j) => j.kinds.includes(kind))
  if (owners.length === 1) return owners[0].code

  // Shared by several countries, so only the number itself can say which.
  const prefix = value.trim().toUpperCase().slice(0, 2)
  if (!/^[A-Z]{2}$/.test(prefix)) return null
  const code = VAT_PREFIX_ALIASES[prefix] ?? prefix
  return isKnownRegion(code) ? code : null
}

/** The flag to show beside a stored document, or an empty string for none. */
export function taxIdFlag(
  kind: string,
  value: string,
  jurisdictions: JurisdictionKinds[],
): string {
  const code = taxIdCountry(kind, value, jurisdictions)
  return code ? countryFlag(code) : ''
}
