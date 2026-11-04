import { describe, expect, it } from 'vitest'
import type { TFunction } from 'i18next'
import { payeeErrorMessage } from './payee-error-message'

// Echoes the key back, with the fallback when one is given, so a test asserts
// which message was chosen rather than what a locale file happens to say.
const t = ((key: string, fallback?: string) => fallback ?? key) as unknown as TFunction

function apiError(detail: unknown) {
  return { response: { data: { detail } } }
}

describe('payeeErrorMessage', () => {
  it('names the collision when the workspace already has that name', () => {
    expect(payeeErrorMessage(apiError('duplicate_payee_name'), t)).toBe('payees.duplicateName')
  })

  it('leads with the document that failed validation', () => {
    expect(payeeErrorMessage(apiError('invalid_tax_id:cpf:checksum'), t))
      .toBe('CPF: payees.invalidTaxId')
  })

  it('leads with the document that is already on the person', () => {
    expect(payeeErrorMessage(apiError('duplicate_tax_id:cnpj'), t))
      .toBe('CNPJ: payees.duplicateTaxId')
  })

  it('falls back to the caller for a code it does not know', () => {
    expect(payeeErrorMessage(apiError('something_else'), t)).toBeNull()
  })

  // A network failure or a 500 carries no detail; the caller's generic message
  // is the honest thing to show, not a mangled read of an absent field.
  it('falls back to the caller when the error carries no detail', () => {
    expect(payeeErrorMessage(new Error('Network Error'), t)).toBeNull()
    expect(payeeErrorMessage(apiError(undefined), t)).toBeNull()
    expect(payeeErrorMessage(apiError([{ msg: 'field required' }]), t)).toBeNull()
  })
})
