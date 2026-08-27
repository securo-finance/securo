/**
 * The parts of the payee form that are decisions rather than markup.
 *
 * These lived inside `pages/payees.tsx` until the form became a component the
 * transaction editor also opens. Pulling them out is what makes them testable:
 * everything else in the dialog is JSX.
 */
import { formatTaxId } from './tax-id'
import type { Payee, TaxIdKindOption } from '../types'

/** '' means the legal nature was not stated, which is an answer, not a blank. */
export type PayeeFormType = '' | 'person' | 'company'

/** One document row as the form holds it: masked for display, not normalised. */
export type TaxIdRow = { kind: string; value: string }

/** The write body this form produces. Declared here rather than imported from
 *  the API client on purpose: the tests compile under a tsconfig with neither
 *  the `@/*` alias nor the DOM lib, and importing `./api` would drag both in.
 *  Structurally the subset of `PayeeWritePayload` the form fills. */
export interface PayeeWriteFields {
  name: string
  type: 'person' | 'company' | null
  notes: string | undefined
  email: string | null
  phone: string | null
  address: string | null
  website: string | null
  tax_ids: TaxIdRow[]
}

export interface PayeeFormValues {
  name: string
  type: PayeeFormType
  notes: string
  email: string
  phone: string
  address: string
  website: string
  taxIdRows: TaxIdRow[]
}

/** The document rows a form should open with.
 *
 *  Every stored document becomes a row, including kinds this jurisdiction does
 *  not ask for: a German VAT number on a Brazilian workspace is a normal state,
 *  and hiding it would be worse than showing it. A payee with none starts on
 *  the local jurisdiction's primary document, or on nothing at all when the
 *  workspace has not named a jurisdiction.
 */
export function seedTaxIdRows(
  payee: Payee | null | undefined,
  kinds: TaxIdKindOption[],
  jurisdiction: string | null,
): TaxIdRow[] {
  const existing = (payee?.tax_ids ?? []).map((doc) => ({
    kind: doc.kind,
    value: formatTaxId(doc.value, kinds.find((k) => k.kind === doc.kind)?.mask ?? null),
  }))
  if (existing.length > 0) return existing
  const primary = jurisdiction ? kinds.filter((k) => k.offered)[0] : undefined
  return primary ? [{ kind: primary.kind, value: '' }] : []
}

/** Turn form state into the request body. */
export function buildPayeeWritePayload(form: PayeeFormValues): PayeeWriteFields {
  return {
    name: form.name,
    type: form.type || null,
    notes: form.notes || undefined,
    // An emptied contact field means "drop this", so blanks are sent as null
    // and the server treats them as removals.
    email: form.email.trim() || null,
    phone: form.phone.trim() || null,
    address: form.address.trim() || null,
    website: form.website.trim() || null,
    tax_ids: form.taxIdRows
      .filter((row) => row.value.trim() !== '')
      .map((row) => ({ kind: row.kind, value: row.value })),
  }
}

/** Which document an "add another" row should default to.
 *
 *  The jurisdiction's own documents first, since they cover the overwhelming
 *  majority of rows; anything else only once those are taken.
 */
export function nextTaxIdKind(
  kinds: TaxIdKindOption[],
  offered: TaxIdKindOption[],
  used: Set<string>,
): TaxIdKindOption | undefined {
  return offered.find((k) => !used.has(k.kind)) ?? kinds.find((k) => !used.has(k.kind))
}
