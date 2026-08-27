import { describe, expect, it } from 'vitest'

import {
  buildPayeeWritePayload,
  nextTaxIdKind,
  seedTaxIdRows,
} from './payee-form-utils'
import type { Payee, TaxIdKindOption } from '../types'

const CNPJ: TaxIdKindOption = {
  kind: 'br_cnpj',
  label_key: 'fiscal.kind.br_cnpj',
  mask: '##.###.###/####-##',
  offered: true,
}
const CPF: TaxIdKindOption = {
  kind: 'br_cpf',
  label_key: 'fiscal.kind.br_cpf',
  mask: '###.###.###-##',
  offered: true,
}
const EU_VAT: TaxIdKindOption = {
  kind: 'eu_vat',
  label_key: 'fiscal.kind.eu_vat',
  mask: null,
  offered: false,
}

const KINDS = [CNPJ, CPF, EU_VAT]

function payee(overrides: Partial<Payee> = {}): Payee {
  return {
    id: 'p1',
    user_id: 'u1',
    name: 'Padaria Central',
    type: 'company',
    source: 'sync',
    is_favorite: false,
    notes: null,
    email: null,
    phone: null,
    address: null,
    website: null,
    tax_ids: [],
    created_at: '2026-01-01T00:00:00Z',
    transaction_count: 0,
    ...overrides,
  }
}

describe('seedTaxIdRows', () => {
  it('masks each stored document with its own kind', () => {
    const rows = seedTaxIdRows(
      payee({ tax_ids: [{ kind: 'br_cnpj', value: '12345678000199' }] }),
      KINDS,
      'BR',
    )
    expect(rows).toEqual([{ kind: 'br_cnpj', value: '12.345.678/0001-99' }])
  })

  it('shows a document whose kind this jurisdiction never offers', () => {
    const rows = seedTaxIdRows(
      payee({ tax_ids: [{ kind: 'eu_vat', value: 'DE811907980' }] }),
      KINDS,
      'BR',
    )
    expect(rows).toEqual([{ kind: 'eu_vat', value: 'DE811907980' }])
  })

  it("starts a payee with no documents on the jurisdiction's first offered kind", () => {
    expect(seedTaxIdRows(payee(), KINDS, 'BR')).toEqual([{ kind: 'br_cnpj', value: '' }])
  })

  it('seeds nothing when the workspace has no jurisdiction', () => {
    expect(seedTaxIdRows(payee(), KINDS, null)).toEqual([])
  })

  it('seeds nothing when the jurisdiction offers no kinds', () => {
    expect(seedTaxIdRows(payee(), [EU_VAT], 'BR')).toEqual([])
  })

  it('treats a brand-new payee the same as one with no documents', () => {
    expect(seedTaxIdRows(null, KINDS, 'BR')).toEqual([{ kind: 'br_cnpj', value: '' }])
  })
})

describe('buildPayeeWritePayload', () => {
  const form = {
    name: 'Padaria Central',
    type: '' as const,
    notes: '',
    email: '',
    phone: '',
    address: '',
    website: '',
    taxIdRows: [],
  }

  it('sends an unstated legal nature as null rather than an empty string', () => {
    expect(buildPayeeWritePayload(form).type).toBeNull()
  })

  it('sends an emptied contact field as null so the server clears it', () => {
    const payload = buildPayeeWritePayload({ ...form, email: '   ' })
    expect(payload.email).toBeNull()
  })

  it('trims contact fields', () => {
    const payload = buildPayeeWritePayload({ ...form, website: '  acme.com  ' })
    expect(payload.website).toBe('acme.com')
  })

  it('omits blank notes instead of nulling them', () => {
    // Asymmetric with the contact fields on purpose: this mirrors what the
    // payees page has always sent, and `undefined` leaves notes untouched.
    expect(buildPayeeWritePayload(form).notes).toBeUndefined()
  })

  it('drops document rows the user left empty', () => {
    const payload = buildPayeeWritePayload({
      ...form,
      taxIdRows: [
        { kind: 'br_cnpj', value: '12.345.678/0001-99' },
        { kind: 'br_cpf', value: '  ' },
      ],
    })
    expect(payload.tax_ids).toEqual([
      { kind: 'br_cnpj', value: '12.345.678/0001-99' },
    ])
  })
})

describe('nextTaxIdKind', () => {
  const offered = [CNPJ, CPF]

  it('prefers a document this jurisdiction asks for', () => {
    expect(nextTaxIdKind(KINDS, offered, new Set())).toBe(CNPJ)
  })

  it('skips the kinds already on the form', () => {
    expect(nextTaxIdKind(KINDS, offered, new Set(['br_cnpj']))).toBe(CPF)
  })

  it('falls back to a foreign document once the local ones are used', () => {
    expect(nextTaxIdKind(KINDS, offered, new Set(['br_cnpj', 'br_cpf']))).toBe(EU_VAT)
  })

  it('returns nothing when every kind is already on the form', () => {
    const used = new Set(['br_cnpj', 'br_cpf', 'eu_vat'])
    expect(nextTaxIdKind(KINDS, offered, used)).toBeUndefined()
  })
})
