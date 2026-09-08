import { useTranslation } from 'react-i18next'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { formatCurrency } from '@/lib/format'
import type { InvoiceDocumentPayload } from '@/types'

/**
 * The invoice as a document.
 *
 * Deliberately not a Securo card. This is the artifact the client
 * receives: a sheet of paper, presented on a recessed surface so it
 * reads as paper on a desk rather than as a panel that forgot the
 * theme. It is light in both themes because the printed thing is light
 * in both themes, and the inset around it is what makes that a decision
 * instead of a bug.
 *
 * A deliberate mirror of `services/invoice_pdf.py`: same blocks, same
 * order, same labels. It recomputes nothing — every value was resolved
 * by the server into one structure both renderers read, so the preview
 * and the file cannot drift apart into two opinions.
 *
 * Labels come from the document rather than from i18n. That reads
 * backwards until you remember whose document it is: the sender chose
 * these words, possibly in their client's language, and translating
 * them into the *viewer's* language would rewrite someone else's
 * invoice. The chrome around the sheet stays translated; the sheet
 * does not.
 */

/** Ink, fixed. The sheet does not follow the app theme, so its colours
 *  cannot come from theme tokens. */
const INK = '#18181b'
const MUTED = '#71717a'
const RULE = '#e4e4e7'

/**
 * A4 at 96dpi, and the same 18mm margin the PDF renderer uses.
 *
 * The point is proportion, not pixel-accuracy: a sheet that is merely
 * "a wide card" reads as a web page, and the whole reason to preview a
 * document is to see the shape of the thing the client will hold. The
 * page keeps its full height even when the invoice is two lines long,
 * because that is what a real page does.
 */
const SHEET_WIDTH = 794
const SHEET_HEIGHT = 1123
const SHEET_MARGIN = 68

function Party({
  title,
  name,
  legalName,
  address,
  email,
  taxIds,
}: {
  title: string
  name: string | null
  legalName?: string | null
  address: string | null
  email?: string | null
  taxIds: { label: string; value: string }[]
}) {
  return (
    <div className="min-w-0">
      <div
        className="text-[10px] font-semibold uppercase tracking-[0.14em]"
        style={{ color: MUTED }}
      >
        {title}
      </div>
      {name && (
        <div className="mt-1.5 text-[15px] font-semibold leading-snug break-words">{name}</div>
      )}
      {/* Only when it differs — printing the same string twice reads as a bug. */}
      {legalName && legalName !== name && (
        <div className="text-[13px] leading-relaxed break-words" style={{ color: MUTED }}>
          {legalName}
        </div>
      )}
      {taxIds.map((doc) => (
        <div
          key={`${doc.label}-${doc.value}`}
          className="text-[13px] leading-relaxed tabular-nums"
          style={{ color: MUTED }}
        >
          {doc.label} {doc.value}
        </div>
      ))}
      {address && (
        <div
          className="text-[13px] leading-relaxed whitespace-pre-line break-words"
          style={{ color: MUTED }}
        >
          {address}
        </div>
      )}
      {email && (
        <div className="text-[13px] leading-relaxed break-all" style={{ color: MUTED }}>
          {email}
        </div>
      )}
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div
        className="text-[10px] font-semibold uppercase tracking-[0.14em]"
        style={{ color: MUTED }}
      >
        {label}
      </div>
      <div className="mt-0.5 text-[13px] tabular-nums">{value}</div>
    </div>
  )
}

export function InvoiceDocumentView({ document }: { document: InvoiceDocumentPayload }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const L = document.labels
  const accent = document.accent_color
  const hasPaid = Number(document.amount_paid) > 0

  const money = (value: string) => formatCurrency(Number(value), document.currency, locale)
  const showDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale)

  const totals: { label: string; value: string; strong?: boolean }[] = []
  if (document.lines.length > 0) {
    totals.push({ label: L.subtotal, value: money(document.subtotal) })
  }
  if (Number(document.discount) > 0) {
    totals.push({ label: L.discount, value: `-${money(document.discount)}` })
  }
  if (Number(document.tax_total) > 0) {
    totals.push({ label: L.tax, value: money(document.tax_total) })
  }
  totals.push({ label: L.total, value: money(document.total), strong: true })
  // Paid and balance only once money has moved: on an untouched invoice
  // they restate the total twice and add nothing.
  if (hasPaid) {
    totals.push({ label: L.paid, value: money(document.amount_paid) })
    totals.push({ label: L.balance, value: money(document.balance), strong: true })
  }

  const received = document.direction === 'payable'

  return (
    // The desk: a recessed surface that makes the sheet read as paper.
    <div className="rounded-xl border border-border bg-muted/50 p-3 sm:p-8 overflow-x-auto">
      {/* A payable was written by the supplier, not by us. Saying so
          above the sheet keeps the page from reading as a document we
          issued — the parties are already swapped server-side, but the
          claim needs words, not just an order. */}
      {received && (
        <div
          className="mx-auto mb-3 max-w-[794px] text-xs text-muted-foreground"
          data-testid="document-received-note"
        >
          <span className="font-medium text-foreground">
            {t('invoices.receivedDocument')}
          </span>{' '}
          {t('invoices.receivedDocumentHint')}
        </div>
      )}
      <div
        data-testid="invoice-document"
        className="mx-auto flex flex-col rounded-sm bg-white shadow-[0_1px_2px_rgba(0,0,0,0.08),0_12px_32px_-10px_rgba(0,0,0,0.22)]"
        style={{
          color: INK,
          width: '100%',
          maxWidth: SHEET_WIDTH,
          // Full page height from the small breakpoint up. On a phone a
          // sheet taller than the screen is theatre, so it collapses to
          // its content there.
          minHeight: `min(${SHEET_HEIGHT}px, 141.4vw)`,
          padding: `clamp(28px, 8.5vw, ${SHEET_MARGIN}px)`,
        }}
      >
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            {document.logo_url && (
              <img
                src={document.logo_url}
                alt=""
                className="h-9 w-auto max-w-[132px] object-contain"
                data-testid="document-logo"
              />
            )}
            <h2 className="text-[26px] font-bold tracking-tight leading-none">{L.invoice}</h2>
          </div>
          {document.number && (
            <div
              className="text-[17px] font-bold tabular-nums leading-none pt-1"
              style={{ color: accent }}
              data-testid="document-number"
            >
              {document.number}
            </div>
          )}
        </header>

        <div className="mt-4 h-[2px] rounded-full" style={{ backgroundColor: accent }} />

        <div className="mt-8 grid gap-8 sm:grid-cols-2">
          <Party
            title={L.from}
            name={document.issuer.name}
            legalName={document.issuer.legal_name}
            address={document.issuer.address}
            taxIds={document.issuer.tax_ids}
          />
          <Party
            title={L.billTo}
            name={document.client.name}
            address={document.client.address}
            email={document.client.email}
            taxIds={document.client.tax_ids}
          />
        </div>

        <div className="mt-8 flex flex-wrap gap-x-12 gap-y-4">
          <Field label={L.issueDate} value={showDate(document.issue_date)} />
          <Field label={L.dueDate} value={showDate(document.due_date)} />
          {document.custom_fields.map((field) => (
            <Field key={field.label} label={field.label} value={field.value} />
          ))}
        </div>

        {document.lines.length > 0 ? (
          <table className="mt-9 w-full">
            <thead>
              <tr style={{ borderBottom: `1px solid ${RULE}` }}>
                <th
                  className="pb-2 text-left text-[10px] font-semibold uppercase tracking-[0.14em]"
                  style={{ color: MUTED }}
                >
                  {L.description}
                </th>
                <th
                  className="pb-2 text-right text-[10px] font-semibold uppercase tracking-[0.14em] w-20"
                  style={{ color: MUTED }}
                >
                  {L.quantity}
                </th>
                <th
                  className="pb-2 text-right text-[10px] font-semibold uppercase tracking-[0.14em] w-28"
                  style={{ color: MUTED }}
                >
                  {L.unitPrice}
                </th>
                <th
                  className="pb-2 text-right text-[10px] font-semibold uppercase tracking-[0.14em] w-28"
                  style={{ color: MUTED }}
                >
                  {L.amount}
                </th>
              </tr>
            </thead>
            <tbody>
              {document.lines.map((line, index) => (
                <tr key={index} style={{ borderBottom: `1px solid ${RULE}` }}>
                  <td className="py-3 pr-4 text-[13.5px] leading-snug">{line.description}</td>
                  <td className="py-3 text-right text-[13.5px] tabular-nums">
                    {/* "12 hours", not "12": the unit is what lets the
                        reader check the sum against what was agreed. */}
                    {Number(line.quantity)}
                    {line.unit ? ` ${line.unit}` : ''}
                  </td>
                  <td className="py-3 text-right text-[13.5px] tabular-nums">
                    {money(line.unit_price)}
                  </td>
                  <td className="py-3 text-right text-[13.5px] tabular-nums">
                    {money(line.total)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          // Not an error state: an invoice with no lines is the normal
          // case where the fiscal document was issued somewhere else and
          // this only tracks the money.
          <p
            className="mt-9 text-[13px] leading-relaxed"
            style={{ color: MUTED }}
            data-testid="document-no-lines"
          >
            {t('invoices.document.noLines')}
          </p>
        )}

        <div className="mt-7 flex justify-end">
          <dl className="w-full max-w-[300px]">
            {totals.map((row) => (
              <div
                key={row.label}
                className="flex items-baseline justify-between gap-8 py-1.5"
                style={row.strong ? { borderTop: `1px solid ${RULE}` } : undefined}
              >
                <dt
                  className={row.strong ? 'text-[13px] font-semibold' : 'text-[13px]'}
                  style={row.strong ? undefined : { color: MUTED }}
                >
                  {row.label}
                </dt>
                <dd
                  className="text-[13.5px] tabular-nums"
                  style={
                    row.strong ? { color: accent, fontWeight: 700, fontSize: '15px' } : undefined
                  }
                >
                  {row.value}
                </dd>
              </div>
            ))}
          </dl>
        </div>

        {/* Everything above is the body; from here down is the footer
            band, pushed to the foot of the page by `mt-auto` so it lands
            where the PDF pins it and where a reader looks for it. */}
        <div className="mt-auto pt-10">
        {(document.payment_details || document.notes) && (
          <div className="grid gap-6 sm:grid-cols-2">
            {document.payment_details && (
              <div>
                <div
                  className="text-[10px] font-semibold uppercase tracking-[0.14em]"
                  style={{ color: MUTED }}
                >
                  {L.paymentDetails}
                </div>
                <p className="mt-1.5 text-[13px] leading-relaxed whitespace-pre-line">
                  {document.payment_details}
                </p>
              </div>
            )}
            {document.notes && (
              <div>
                <div
                  className="text-[10px] font-semibold uppercase tracking-[0.14em]"
                  style={{ color: MUTED }}
                >
                  {L.notes}
                </div>
                <p className="mt-1.5 text-[13px] leading-relaxed whitespace-pre-line">
                  {document.notes}
                </p>
              </div>
            )}
          </div>
        )}

        {document.footer_note && (
          <p
            className="mt-6 pt-4 text-[11.5px] leading-relaxed"
            style={{ borderTop: `1px solid ${RULE}`, color: MUTED }}
          >
            {document.footer_note}
          </p>
        )}
        </div>
      </div>
    </div>
  )
}
