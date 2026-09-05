import type { Invoice, InvoiceCustomFieldDef, InvoiceState, InvoiceTemplate } from '@/types'

/**
 * Pure helpers for the invoicing ledger.
 *
 * Everything here is a function of data the server already sent. In
 * particular, none of it recomputes `state`: the server derives it from
 * allocations and the due date, and a second implementation on this side
 * is exactly how the two would come to disagree. What lives here is
 * presentation — how a state looks, what may be done to it, and what the
 * document says its own labels are.
 */

/** How a derived state paints. Keyed by state so a new one is a compile
 *  error here rather than an unstyled badge in production. */
export const STATE_TONE: Record<InvoiceState, string> = {
  draft: 'bg-muted text-muted-foreground border-border',
  open: 'bg-blue-500/10 text-blue-600 border-blue-500/20 dark:text-blue-400',
  partial: 'bg-amber-500/10 text-amber-600 border-amber-500/20 dark:text-amber-400',
  paid: 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20 dark:text-emerald-400',
  overdue: 'bg-red-500/10 text-red-600 border-red-500/20 dark:text-red-400',
  void: 'bg-muted text-muted-foreground border-border line-through',
  uncollectible: 'bg-muted text-muted-foreground border-border',
}

/** The states that represent money still expected. Used for the "open"
 *  filter and for anything that counts receivables. `uncollectible` is
 *  deliberately absent — it is a decision to stop expecting. */
export const OUTSTANDING_STATES: InvoiceState[] = ['open', 'partial', 'overdue']

export function isOutstanding(invoice: Invoice): boolean {
  return OUTSTANDING_STATES.includes(invoice.state)
}

/** Which actions the UI should offer. Mirrors the server's rules so the
 *  user is never shown a button that will come back 400 — the server
 *  still enforces every one of these, this only avoids the dead end. */
export function availableActions(invoice: Invoice): {
  canEdit: boolean
  canDelete: boolean
  canIssue: boolean
  canVoid: boolean
  canWriteOff: boolean
  canReopen: boolean
  canAllocate: boolean
} {
  const isDraft = invoice.status === 'draft'
  const isOpen = invoice.status === 'open'
  const hasMoney = invoice.allocations.length > 0
  const settled = Number(invoice.balance) <= 0
  return {
    // Financial fields are frozen once issued; notes stay editable, which
    // is why the detail view keeps a notes field either way.
    canEdit: isDraft,
    // An issued invoice is never deleted. Void keeps the paper trail.
    canDelete: isDraft,
    canIssue: isDraft,
    // Voiding with money attached would strand the allocation.
    canVoid: isOpen && !hasMoney,
    canWriteOff: isOpen,
    canReopen: invoice.status === 'uncollectible',
    canAllocate: isOpen && !settled,
  }
}

/** The number as a person reads it: the prefix the document carried plus
 *  the sequence. Drafts have no number at all, and saying so beats
 *  printing a placeholder that looks like one.
 *
 *  The snapshot is authoritative whenever it exists — including when it
 *  recorded *no* prefix, which is a real answer and not a missing one.
 *  An invoice issued as "2" must keep reading as "2" after someone sets
 *  a prefix, or the app renames a document the client already has.
 *  Live settings are consulted only when there is no snapshot at all.
 *
 *  None of that applies to a document we did not write. An import arrives
 *  already named, and that name is reproduced verbatim from
 *  `external_number` — ours would rename a supplier's reference. */
export function displayNumber(invoice: Invoice, prefix?: string | null): string | null {
  if (invoice.origin === 'imported') return invoice.external_number || null
  if (invoice.number == null) return null
  const resolved = invoice.snapshot
    ? ((invoice.snapshot.number_prefix as string | null) ?? '')
    : (prefix ?? '')
  return `${resolved}${invoice.number}`
}

/** Labels come from the issued document when there is one, and from live
 *  settings while it is still a draft. */
export function resolveTemplate(
  invoice: Invoice | null,
  settingsTemplate: InvoiceTemplate | null | undefined,
): InvoiceTemplate {
  const frozen = invoice?.snapshot?.template as InvoiceTemplate | undefined
  return frozen ?? settingsTemplate ?? {}
}

export function customFieldDefs(template: InvoiceTemplate | null | undefined): InvoiceCustomFieldDef[] {
  const defs = template?.custom_fields
  return Array.isArray(defs) ? defs.filter((d) => d && typeof d.key === 'string') : []
}

/** Total of the lines, computed client-side purely so the draft form can
 *  show a running total while typing. The server recomputes on save and
 *  its answer is the one that is stored. */
export function linesTotal(lines: { quantity: string; unit_price: string }[]): number {
  return lines.reduce((sum, line) => {
    const quantity = Number(line.quantity)
    const price = Number(line.unit_price)
    if (!Number.isFinite(quantity) || !Number.isFinite(price)) return sum
    return sum + quantity * price
  }, 0)
}

/** Days until due, negative once past. Used for the "vence em 3 dias"
 *  hint; `days_overdue` from the server is the authority once it is
 *  actually late. */
export function daysUntilDue(invoice: Invoice, today = new Date()): number {
  const due = new Date(`${invoice.due_date}T00:00:00`)
  const reference = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  return Math.round((due.getTime() - reference.getTime()) / 86_400_000)
}

/** Map a server rule violation onto a translation key. The server sends
 *  a stable `code`; matching on the English message would break the
 *  moment either side is reworded. */
export function invoiceErrorKey(error: unknown): string | null {
  const detail = (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data
    ?.detail
  const code = detail && typeof detail === 'object' ? detail.code : undefined
  return code ? `invoices.errors.${code}` : null
}

/** A person, not a strategy. Mirrors `MANUAL_METHOD` on the server. */
export const MANUAL_METHOD = 'manual'

/**
 * How to describe the origin of an allocation.
 *
 * `method` is either `manual` or the **id of the matching strategy** that
 * produced the link — ids that come from the reconciliation policy, a
 * document that will eventually be user-editable. So this deliberately
 * does not switch on a closed list: it separates "a person did this" from
 * "a rule did this", and hands the id back so the caller can show *which*
 * rule once the policy is readable from the client.
 *
 * Until then an unknown id still reads as an explanation rather than as a
 * mystery, which is the whole reason the column stores it.
 */
export function allocationOrigin(method: string): {
  automatic: boolean
  strategyId: string | null
} {
  const automatic = method !== MANUAL_METHOD
  return { automatic, strategyId: automatic ? method : null }
}


/** A file size as a person reads it. Bytes below a kilobyte, then whole
 *  kilobytes, then one decimal of a megabyte — the precision stops where
 *  it stops being information. */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B'
  if (bytes < 1024) return `${Math.round(bytes)} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** How a filed document can be shown in the page.
 *
 *  `pdf` and `image` render in place; everything else has to be opened,
 *  because guessing at a preview for a format the browser cannot draw
 *  produces an empty frame rather than an answer. */
export function previewKind(contentType: string | null | undefined): 'pdf' | 'image' | 'none' {
  const type = (contentType ?? '').toLowerCase()
  if (type === 'application/pdf') return 'pdf'
  if (type.startsWith('image/')) return 'image'
  return 'none'
}

/** Whether the Document tab has a real page to show.
 *
 *  Three outcomes, and the middle one is the point: an imported invoice
 *  with nothing filed must show neither a file (there is none) nor our
 *  own render (we did not write it, and drawing one invents a document
 *  somebody else issued). */
export function documentSource(
  origin: string,
  sourceFile: unknown | null | undefined,
): 'filed' | 'missing' | 'rendered' {
  if (sourceFile) return 'filed'
  return origin === 'imported' ? 'missing' : 'rendered'
}

/** Where a filed document came from, as a person reads it.
 *
 *  A source id is a machine name (`stripe`, `nfe`, `email`), so it is
 *  shown as it is rather than translated — renaming somebody's system in
 *  our own words helps nobody trying to work out which integration
 *  delivered which file. Only the absence of one is our word: a file
 *  with no source was uploaded here by hand.
 */
export function documentProvenance(
  source: string | null | undefined,
): { kind: 'uploaded' } | { kind: 'system'; name: string } {
  const name = (source ?? '').trim()
  return name ? { kind: 'system', name } : { kind: 'uploaded' }
}
