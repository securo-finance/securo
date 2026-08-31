import type { TFunction } from 'i18next'

/** Turn the server's machine-readable rejection into something a person can
 *  act on.
 *
 *  The codes carry the part the user has to look at — which document, or that
 *  the name is taken — and the sentence around it is translated here, because
 *  the server has no idea what language anyone reads. Anything else returns
 *  null so the caller falls back to its generic message rather than putting a
 *  raw code, or a stack of English prose, in front of someone. */
export function payeeErrorMessage(error: unknown, t: TFunction): string | null {
  const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  if (typeof detail !== 'string') return null

  if (detail === 'duplicate_payee_name') return t('payees.duplicateName')

  // `invalid_tax_id:<kind>:<reason>` and `duplicate_tax_id:<kind>`. The reason
  // is useful in logs; the document name is what the user needs on screen.
  const [code, kind = ''] = detail.split(':')
  if (code === 'invalid_tax_id' || code === 'duplicate_tax_id') {
    const name = t(`fiscal.kind.${kind}`, kind.toUpperCase())
    const message = code === 'invalid_tax_id' ? 'payees.invalidTaxId' : 'payees.duplicateTaxId'
    return `${name}: ${t(message)}`
  }

  return null
}
