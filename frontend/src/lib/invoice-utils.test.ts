import { describe, expect, it } from 'vitest'
import {
  OUTSTANDING_STATES,
  STATE_TONE,
  availableActions,
  customFieldDefs,
  daysUntilDue,
  displayNumber,
  documentProvenance,
  documentSource,
  formatFileSize,
  invoiceErrorKey,
  isOutstanding,
  linesTotal,
  previewKind,
  resolveTemplate,
} from './invoice-utils'
import type { Invoice, InvoiceState } from '@/types'

/** A minimal invoice, overridable per test. Defaults to the shape the
 *  tracking preset produces: issued, unpaid, no document. */
function makeInvoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: 'inv-1',
    payee_id: null,
    payee: null,
    document_type: 'invoice',
    direction: 'receivable',
    origin: 'local',
    external_source: null,
    external_id: null,
    number: 7,
    series: null,
    external_number: null,
    status: 'open',
    state: 'open',
    issue_date: '2026-08-01',
    due_date: '2026-08-31',
    competence_date: '2026-08-01',
    sent_at: null,
    currency: 'USD',
    subtotal: '0.00',
    discount: '0.00',
    tax_total: '0.00',
    total: '1000.00',
    amount_paid: '0.00',
    balance: '1000.00',
    days_overdue: 0,
    notes: null,
    internal_notes: null,
    custom_fields: null,
    snapshot: null,
    share_token: null,
    lines: [],
    allocations: [],
    created_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

describe('STATE_TONE', () => {
  it('styles every state the server can send', () => {
    // A state without a tone renders unstyled in production. Keeping the
    // list here means adding one is a failing test, not a silent gap.
    const states: InvoiceState[] = [
      'draft', 'open', 'partial', 'paid', 'overdue', 'void', 'uncollectible',
    ]
    for (const state of states) expect(STATE_TONE[state]).toBeTruthy()
  })

  it('gives void a struck-through treatment', () => {
    expect(STATE_TONE.void).toContain('line-through')
  })
})

describe('isOutstanding', () => {
  it('counts the three states where money is still expected', () => {
    expect(OUTSTANDING_STATES).toEqual(['open', 'partial', 'overdue'])
    for (const state of OUTSTANDING_STATES) {
      expect(isOutstanding(makeInvoice({ state }))).toBe(true)
    }
  })

  it('excludes uncollectible — a decision to stop expecting', () => {
    expect(isOutstanding(makeInvoice({ state: 'uncollectible' }))).toBe(false)
  })

  it('excludes drafts, paid and void', () => {
    for (const state of ['draft', 'paid', 'void'] as InvoiceState[]) {
      expect(isOutstanding(makeInvoice({ state }))).toBe(false)
    }
  })
})

describe('availableActions', () => {
  it('lets a draft be edited, deleted and issued — and nothing else', () => {
    const actions = availableActions(makeInvoice({ status: 'draft', state: 'draft', number: null }))
    expect(actions).toEqual({
      canEdit: true, canDelete: true, canIssue: true,
      canVoid: false, canWriteOff: false, canReopen: false, canAllocate: false,
    })
  })

  it('freezes an issued invoice: no edit, no delete', () => {
    const actions = availableActions(makeInvoice())
    expect(actions.canEdit).toBe(false)
    // An issued invoice is never deleted — void keeps the paper trail.
    expect(actions.canDelete).toBe(false)
    expect(actions.canVoid).toBe(true)
    expect(actions.canWriteOff).toBe(true)
    expect(actions.canAllocate).toBe(true)
  })

  it('refuses to offer void once money is linked', () => {
    // Voiding would strand the allocation; the server refuses it too.
    const actions = availableActions(
      makeInvoice({
        balance: '500.00',
        allocations: [
          { id: 'a1', transaction_id: 't1', credit_note_id: null, amount: '500.00',
            method: 'manual', allocated_at: '2026-08-10T00:00:00Z', transaction: null },
        ],
      }),
    )
    expect(actions.canVoid).toBe(false)
    expect(actions.canAllocate).toBe(true)
  })

  it('stops offering allocation once the balance reaches zero', () => {
    const actions = availableActions(makeInvoice({ balance: '0.00', state: 'paid' }))
    expect(actions.canAllocate).toBe(false)
  })

  it('offers reopen only on an uncollectible invoice', () => {
    expect(availableActions(makeInvoice({ status: 'uncollectible' })).canReopen).toBe(true)
    expect(availableActions(makeInvoice({ status: 'open' })).canReopen).toBe(false)
  })

  it('offers nothing destructive on a voided invoice', () => {
    const actions = availableActions(makeInvoice({ status: 'void', state: 'void' }))
    expect(Object.values(actions).every((v) => v === false)).toBe(true)
  })
})

describe('displayNumber', () => {
  it('says a draft has no number rather than inventing one', () => {
    expect(displayNumber(makeInvoice({ number: null }))).toBeNull()
  })

  it('uses live settings while there is no snapshot', () => {
    expect(displayNumber(makeInvoice({ snapshot: null }), 'FAT-')).toBe('FAT-7')
  })

  it('prefers the prefix frozen into the document', () => {
    const invoice = makeInvoice({ snapshot: { number_prefix: 'INV/' } })
    expect(displayNumber(invoice, 'FAT-')).toBe('INV/7')
  })

  it('never puts our prefix on a document we did not write', () => {
    const imported = makeInvoice({
      origin: 'imported',
      external_number: 'FAT-9931',
      number: null,
      snapshot: null,
    })
    expect(displayNumber(imported, 'INV-')).toBe('FAT-9931')
  })

  it('reproduces a padded source name our own column could not hold', () => {
    const imported = makeInvoice({
      origin: 'imported',
      external_number: '2026/A/0031',
      number: null,
      snapshot: null,
    })
    expect(displayNumber(imported, 'INV-')).toBe('2026/A/0031')
  })

  it('says nothing when the source named the document nothing', () => {
    const imported = makeInvoice({ origin: 'imported', external_number: null, number: null })
    expect(displayNumber(imported, 'INV-')).toBeNull()
  })

  it('treats a snapshot with no prefix as a real answer, not a gap', () => {
    // The regression this exists for: an invoice issued as "7", before
    // anyone set a prefix, must not start reading as "FAT-7" afterwards.
    // The client already has a document numbered 7.
    const invoice = makeInvoice({ snapshot: { number_prefix: null } })
    expect(displayNumber(invoice, 'FAT-')).toBe('7')
  })

  it('renders bare when neither side has a prefix', () => {
    expect(displayNumber(makeInvoice({ snapshot: {} }), null)).toBe('7')
  })
})

describe('resolveTemplate', () => {
  const live = { labels: { quantity: 'Units' } }
  const frozen = { labels: { quantity: 'Hours' } }

  it('uses the frozen template once the document is issued', () => {
    const invoice = makeInvoice({ snapshot: { template: frozen } })
    expect(resolveTemplate(invoice, live).labels?.quantity).toBe('Hours')
  })

  it('falls back to live settings while the invoice is still a draft', () => {
    expect(resolveTemplate(makeInvoice({ snapshot: null }), live).labels?.quantity).toBe('Units')
  })

  it('returns an empty template rather than undefined', () => {
    expect(resolveTemplate(null, null)).toEqual({})
  })
})

describe('customFieldDefs', () => {
  it('reads the definitions a workspace declared', () => {
    const defs = customFieldDefs({ custom_fields: [{ key: 'po', label: 'PO number' }] })
    expect(defs).toHaveLength(1)
    expect(defs[0].key).toBe('po')
  })

  it('survives a template with no definitions', () => {
    expect(customFieldDefs(null)).toEqual([])
    expect(customFieldDefs({})).toEqual([])
  })

  it('drops malformed entries instead of rendering a broken field', () => {
    // The template is free-form jsonb by design, so it can contain
    // anything a previous version or a hand edit put there.
    const defs = customFieldDefs({
      custom_fields: [{ key: 'ok', label: 'Fine' }, null as never, { label: 'no key' } as never],
    })
    expect(defs.map((d) => d.key)).toEqual(['ok'])
  })
})

describe('linesTotal', () => {
  it('multiplies quantity by price across lines', () => {
    expect(linesTotal([
      { quantity: '10', unit_price: '150.00' },
      { quantity: '2', unit_price: '25.50' },
    ])).toBe(1551)
  })

  it('ignores lines that are still half-typed', () => {
    // The form calls this on every keystroke; "1." and "" are normal
    // intermediate states, not errors to surface.
    expect(linesTotal([{ quantity: '', unit_price: 'abc' }, { quantity: '2', unit_price: '10' }])).toBe(20)
  })

  it('is zero for no lines', () => {
    expect(linesTotal([])).toBe(0)
  })
})

describe('daysUntilDue', () => {
  const today = new Date(2026, 7, 26) // 2026-08-26, local

  it('counts forward to a future due date', () => {
    expect(daysUntilDue(makeInvoice({ due_date: '2026-08-31' }), today)).toBe(5)
  })

  it('goes negative once the date has passed', () => {
    expect(daysUntilDue(makeInvoice({ due_date: '2026-08-14' }), today)).toBe(-12)
  })

  it('is zero on the day itself', () => {
    expect(daysUntilDue(makeInvoice({ due_date: '2026-08-26' }), today)).toBe(0)
  })
})

describe('invoiceErrorKey', () => {
  it('maps the server code onto a translation key', () => {
    const error = { response: { data: { detail: { code: 'over_allocation' } } } }
    expect(invoiceErrorKey(error)).toBe('invoices.errors.over_allocation')
  })

  it('returns null for anything that is not a ledger rule', () => {
    // Matching on the English message would break the moment either
    // side is reworded, so an unrecognised shape must fall through to
    // the generic toast rather than guess.
    expect(invoiceErrorKey(new Error('network'))).toBeNull()
    expect(invoiceErrorKey({ response: { data: { detail: 'plain string' } } })).toBeNull()
    expect(invoiceErrorKey(undefined)).toBeNull()
  })
})


describe('formatFileSize', () => {
  it('counts bytes below a kilobyte', () => {
    expect(formatFileSize(0)).toBe('0 B')
    expect(formatFileSize(976)).toBe('976 B')
  })

  it('rounds to whole kilobytes, then to a tenth of a megabyte', () => {
    expect(formatFileSize(2048)).toBe('2 KB')
    expect(formatFileSize(1024 * 1024 * 3.25)).toBe('3.3 MB')
  })

  it('does not print nonsense for a size that never arrived', () => {
    expect(formatFileSize(Number.NaN)).toBe('0 B')
    expect(formatFileSize(-1)).toBe('0 B')
  })
})

describe('previewKind', () => {
  it('renders a PDF and an image in place', () => {
    expect(previewKind('application/pdf')).toBe('pdf')
    expect(previewKind('image/png')).toBe('image')
    expect(previewKind('IMAGE/JPEG')).toBe('image')
  })

  it('offers no preview for a format the browser cannot draw', () => {
    // An empty frame is worse than a button that opens the file.
    expect(previewKind('application/xml')).toBe('none')
    expect(previewKind(null)).toBe('none')
    expect(previewKind(undefined)).toBe('none')
  })
})

describe('documentSource', () => {
  it('shows the filed file whenever there is one', () => {
    expect(documentSource('imported', { id: 'a' })).toBe('filed')
    expect(documentSource('local', { id: 'a' })).toBe('filed')
  })

  it('renders our own page only for a document we wrote', () => {
    expect(documentSource('local', null)).toBe('rendered')
  })

  it('draws nothing for an import with nothing filed', () => {
    // The case the aggregator exists for: a blank sheet in our layout,
    // standing in for a document a supplier issued, is an invention.
    expect(documentSource('imported', null)).toBe('missing')
    expect(documentSource('imported', undefined)).toBe('missing')
  })
})


describe('documentProvenance', () => {
  it('names the system that delivered the file, as that system names itself', () => {
    // Not translated: renaming somebody's integration in our own words
    // helps nobody working out which one delivered which file.
    expect(documentProvenance('stripe')).toEqual({ kind: 'system', name: 'stripe' })
    expect(documentProvenance('nfe-provider')).toEqual({ kind: 'system', name: 'nfe-provider' })
  })

  it('treats no source as a person having uploaded it', () => {
    expect(documentProvenance(null)).toEqual({ kind: 'uploaded' })
    expect(documentProvenance(undefined)).toEqual({ kind: 'uploaded' })
    expect(documentProvenance('   ')).toEqual({ kind: 'uploaded' })
  })
})
