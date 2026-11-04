/**
 * Emoji flag for an ISO 3166-1 alpha-2 country code.
 *
 * Derived from the code rather than stored in a lookup table: a flag is just
 * the two letters shifted into Unicode's regional indicator block, so every
 * country a jurisdiction pack is added for gets one without touching this
 * file.
 *
 * Returns an empty string for anything that is not exactly two letters.
 * `Workspace.tax_jurisdiction` accepts a regime code as well as a country
 * code, and a regime has no flag.
 */
const REGIONAL_INDICATOR_A = 0x1f1e6
const LETTER_A = 'A'.charCodeAt(0)

export function countryFlag(code: string): string {
  const upper = code.trim().toUpperCase()
  if (!/^[A-Z]{2}$/.test(upper)) return ''
  return String.fromCodePoint(
    ...[...upper].map((letter) => REGIONAL_INDICATOR_A + letter.charCodeAt(0) - LETTER_A),
  )
}
