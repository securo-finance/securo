/**
 * Display formatting for fiscal documents.
 *
 * Masks come from the server (`/api/fiscal/tax-id-kinds`) rather than being
 * duplicated here, because the same definition also drives validation. This
 * file only knows how to *apply* a mask, never which mask a document uses.
 *
 * A mask is a template where `#` accepts one digit and every other character
 * is a literal, so `##.###.###/####-##` turns 14 digits into a CNPJ. A kind
 * with no mask is shown exactly as typed, which is what keeps a jurisdiction
 * nobody has described from being mangled.
 */

/** Apply a mask as the user types, ignoring anything the mask has no slot for. */
export function applyMask(raw: string, mask: string | null): string {
  if (!mask) return raw
  const digits = raw.replace(/\D/g, '')
  let out = ''
  let index = 0
  for (const char of mask) {
    if (index >= digits.length) break
    if (char === '#') {
      out += digits[index]
      index += 1
    } else {
      out += char
    }
  }
  return out
}

/** Format a stored (normalised) value for display. */
export function formatTaxId(value: string, mask: string | null): string {
  if (!mask) return value
  return applyMask(value, mask)
}
