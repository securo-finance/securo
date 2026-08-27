import { describe, expect, it } from 'vitest'
import type { TFunction } from 'i18next'

import { payeeErrorMessage } from './payee-errors'

/** Echoes the key, or the caller's fallback when it passes one — enough to
 *  see which message was chosen without loading the locale files. */
const t = ((key: string, fallback?: string) => fallback ?? key) as unknown as TFunction

function apiError(detail: unknown): unknown {
  return { response: { data: { detail } } }
}

describe('payeeErrorMessage', () => {
  it('names the document that failed validation', () => {
    expect(payeeErrorMessage(apiError('invalid_tax_id:br_cnpj:checksum'), t)).toBe(
      'BR_CNPJ: payees.invalidTaxId',
    )
  })

  it('falls back to the raw kind when the detail carries no reason', () => {
    expect(payeeErrorMessage(apiError('invalid_tax_id:br_cpf'), t)).toBe(
      'BR_CPF: payees.invalidTaxId',
    )
  })

  it('explains a name collision instead of leaving a bare error', () => {
    // The server has no machine-readable code for this one, so the English
    // sentinel is all there is to match on.
    expect(payeeErrorMessage(apiError('A payee with this name already exists'), t)).toBe(
      'payees.duplicateName',
    )
  })

  it('recognises the collision whatever case the server uses', () => {
    expect(payeeErrorMessage(apiError('A Payee With This Name Already Exists'), t)).toBe(
      'payees.duplicateName',
    )
  })

  it('defers to the caller for a detail it does not recognise', () => {
    expect(payeeErrorMessage(apiError('Payee not found'), t)).toBeNull()
  })

  it('defers to the caller when the response carries no detail', () => {
    expect(payeeErrorMessage({ response: { data: {} } }, t)).toBeNull()
  })

  it('defers to the caller for a non-string detail', () => {
    expect(payeeErrorMessage(apiError([{ msg: 'Field required' }]), t)).toBeNull()
  })

  it('survives an error that never reached the server', () => {
    expect(payeeErrorMessage(new Error('Network Error'), t)).toBeNull()
  })
})
