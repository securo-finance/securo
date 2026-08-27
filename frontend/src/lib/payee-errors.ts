/** Turn a payee write failure into something a person can act on. */
import type { TFunction } from 'i18next'

export function payeeErrorMessage(error: unknown, t: TFunction): string | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail !== 'string') return null

  // Documents arrive as `invalid_tax_id:<kind>:<reason>`; the reason is useful
  // in logs, the document name is what the user needs to look at.
  if (detail.startsWith('invalid_tax_id:')) {
    const kind = detail.split(':')[1] ?? ''
    const name = t(`fiscal.kind.${kind}`, kind.toUpperCase())
    return `${name}: ${t('payees.invalidTaxId')}`
  }

  // Name collisions have no machine-readable code — payee_service.py:185,227
  // raises a bare ValueError that the API returns verbatim as the 400 detail.
  // Matching the English sentinel is brittle to a backend reword, but the
  // alternative is the bare "Error" this used to show, which tells the user
  // nothing about the one mistake this form actually invites.
  if (detail.toLowerCase().includes('already exists')) return t('payees.duplicateName')

  return null
}
