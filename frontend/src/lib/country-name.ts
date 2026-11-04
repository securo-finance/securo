/**
 * Localised country name for an ISO 3166-1 alpha-2 code.
 *
 * Resolved through `Intl.DisplayNames`, which every supported browser ships
 * with the CLDR data already loaded, rather than through the translation
 * files. Two reasons:
 *
 *   - Country names are not product copy. Hand-translating forty of them into
 *     ten locales would be four hundred strings that only restate what the
 *     platform already knows, and each new jurisdiction pack would add ten
 *     more.
 *   - It is correct in more locales than we translate into. The browser
 *     answers in the user's own language even where Securo has no locale
 *     file, and it declines and capitalises properly, which a flat string
 *     table cannot do.
 *
 * Falls back to the bare code, so a value `Intl` does not recognise still
 * renders as something rather than as nothing. `Workspace.tax_jurisdiction`
 * accepts a regime code as well as a country code, and no regime has a name
 * here.
 */
const cache = new Map<string, Intl.DisplayNames | null>()

function displayNames(locale: string): Intl.DisplayNames | null {
  if (!cache.has(locale)) {
    try {
      cache.set(locale, new Intl.DisplayNames([locale], { type: 'region' }))
    } catch {
      // An unsupported locale tag, or an environment without the region data.
      cache.set(locale, null)
    }
  }
  return cache.get(locale) ?? null
}

/** CLDR's sentinel for "we do not know", which is a code but not a country. */
const UNKNOWN_REGION = 'ZZ'

let strict: Intl.DisplayNames | null | undefined

/**
 * Whether a two-letter code names a country at all.
 *
 * Uses `fallback: 'none'`, which answers `undefined` for a code the region
 * data does not know, rather than echoing the code back the way `countryName`
 * deliberately does. Needed wherever a code is *derived* rather than given —
 * a VAT number's prefix, say — so a stray pair of letters does not get
 * dressed up as a country.
 */
export function isKnownRegion(code: string): boolean {
  const upper = code.trim().toUpperCase()
  if (!/^[A-Z]{2}$/.test(upper) || upper === UNKNOWN_REGION) return false
  if (strict === undefined) {
    try {
      strict = new Intl.DisplayNames(['en'], { type: 'region', fallback: 'none' })
    } catch {
      strict = null
    }
  }
  // With no region data available, no claim can be made either way; treating
  // the code as unknown keeps a wrong flag off the screen.
  return strict ? strict.of(upper) !== undefined : false
}

export function countryName(code: string, locale: string): string {
  const upper = code.trim().toUpperCase()
  if (!/^[A-Z]{2}$/.test(upper)) return code
  try {
    return displayNames(locale)?.of(upper) ?? upper
  } catch {
    // `of` throws on a structurally invalid code rather than returning
    // undefined.
    return upper
  }
}
