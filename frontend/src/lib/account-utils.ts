export function getAccountName(account: { name: string; display_name?: string | null }): string {
  return account.display_name ?? account.name
}

/**
 * The bank's identifier for an account, masked to its last 4 chars, e.g. "•••• 1234".
 *
 * Banks commonly report every account under the same label (often the holder's
 * name), so this is what tells two of them apart. Null when the provider gave us
 * no identifier, so callers render nothing rather than an empty mask. Locale-neutral
 * by construction: dots and the bank's own digits, nothing to translate.
 */
export function formatAccountMask(account: { masked_number?: string | null }): string | null {
  return account.masked_number ? `•••• ${account.masked_number}` : null
}

/**
 * Account name with its mask appended, e.g. "Checking •••• 1234", for compact
 * single-line surfaces such as the account <select> options, where there is no
 * room for a secondary line. Unparenthesized so callers that already append
 * their own parenthetical (the transfer dialog appends the currency) don't end
 * up with two of them.
 */
export function getAccountLabel(account: {
  name: string
  display_name?: string | null
  masked_number?: string | null
}): string {
  const mask = formatAccountMask(account)
  const name = getAccountName(account)
  return mask ? `${name} ${mask}` : name
}
