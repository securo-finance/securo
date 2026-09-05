import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { FileText, Image as ImageIcon, Plus, Star, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { IconAction, SectionCard } from '@/components/invoice-ui'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { invoices as invoicesApi } from '@/lib/api'
import { documentProvenance, formatFileSize, previewKind } from '@/lib/invoice-utils'
import { useDateLocale } from '@/hooks/use-display-locale'
import { cn } from '@/lib/utils'
import type { InvoiceAttachment, InvoiceAttachmentKind } from '@/types'

/**
 * Everything that stands as this invoice's paperwork, in one place.
 *
 * An invoice is often a folder: the supplier's bill arrives by email, the
 * fiscal document follows from a portal, the receipt after that. Reading
 * any of them used to mean opening one browser tab per file, which is no
 * way to check whether the fiscal document matches the bill.
 *
 * So the files sit down the left and the selected one is rendered on the
 * right. The page we generate is an entry in that same list when we are
 * the ones who wrote the invoice: it is one of the documents, not a
 * different kind of thing living elsewhere on the screen.
 */

const KINDS: InvoiceAttachmentKind[] = ['bill', 'fiscal', 'receipt', 'contract', 'other']

/** The generated page is addressed by a reserved id, so selection stays a
 *  single string instead of a union the whole component has to narrow. */
const OUR_PAGE = 'our-page'

export function InvoiceDocumentBrowser({
  invoiceId,
  origin,
  canWrite,
  ourPageLabel,
  ourPageDate,
  ourPage,
  onChanged,
}: {
  invoiceId: string
  origin: string
  canWrite: boolean
  /** The number the generated page carries, when it has one. */
  ourPageLabel?: string | null
  /** When the page came into being — the moment the invoice was issued,
   *  which is also the date printed on it. The other entries say when
   *  they arrived; this one says when it was made. */
  ourPageDate?: string | null
  /** The rendered invoice. Passed in so this component never learns how a
   *  document is drawn — only that one exists. */
  ourPage: React.ReactNode | null
  onChanged?: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const dateLocale = useDateLocale()
  const fileInput = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState<InvoiceAttachmentKind>('bill')
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  const { data: attachments = [] } = useQuery({
    queryKey: ['invoice-attachments', invoiceId],
    queryFn: () => invoicesApi.attachments.list(invoiceId),
  })

  // An import with nothing filed has no page at all: drawing ours would
  // invent a document a supplier issued and we were never handed.
  const showOurPage = origin !== 'imported' && ourPage !== null

  const active = useMemo(() => {
    if (selected === OUR_PAGE && showOurPage) return OUR_PAGE
    if (selected && attachments.some((a) => a.id === selected)) return selected
    // Falls to the file that *is* the document, then to our own page,
    // then to whatever was filed first.
    const primary = attachments.find((a) => a.is_primary)
    if (primary) return primary.id
    if (showOurPage) return OUR_PAGE
    return attachments[0]?.id ?? null
  }, [selected, attachments, showOurPage])

  const refresh = () => {
    // Cleared on any success: a refused file leaves a message on screen,
    // and if it outlives the next working action it stops being true.
    setError(null)
    void queryClient.invalidateQueries({ queryKey: ['invoice-attachments', invoiceId] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-document', invoiceId] })
    onChanged?.()
  }

  const uploadMutation = useMutation({
    mutationFn: (file: File) => invoicesApi.attachments.upload(invoiceId, file, { kind }),
    onSuccess: (created: InvoiceAttachment) => {
      // Show what was just added. Uploading and then hunting for the file
      // in the list is a step the person did not ask for.
      setSelected(created.id)
      refresh()
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? t('invoices.documents.uploadFailed'))
    },
  })

  const primaryMutation = useMutation({
    mutationFn: (id: string) =>
      invoicesApi.attachments.update(invoiceId, id, { is_primary: true }),
    onSuccess: refresh,
  })

  const removeMutation = useMutation({
    mutationFn: (id: string) => invoicesApi.attachments.remove(invoiceId, id),
    onSuccess: (_data, id) => {
      if (selected === id) setSelected(null)
      refresh()
    },
  })

  const longDate = (value: Date) =>
    value.toLocaleDateString(dateLocale, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    })
  /** When the file reached us, which is rarely the date on the document
   *  itself — an integration can deliver a fiscal document weeks late.
   *  A full timestamp, so it is read in the reader's own zone. */
  const showArrival = (value: string) => longDate(new Date(value))
  /** A date with no time. Parsed at local midnight rather than handed to
   *  `new Date`, which reads a bare `2026-08-28` as UTC and lands on the
   *  27th for anybody west of Greenwich. */
  const showPlainDate = (value: string) => longDate(new Date(`${value}T00:00:00`))

  const activeAttachment =
    active && active !== OUR_PAGE ? attachments.find((a) => a.id === active) : undefined
  const anyPrimary = attachments.some((a) => a.is_primary)

  return (
    <SectionCard>
      <div className="flex flex-col lg:flex-row">
        {/* The shelf */}
        <div className="lg:w-72 xl:w-80 shrink-0 border-b lg:border-b-0 lg:border-r border-border flex flex-col">
          <div className="px-3 py-3 border-b border-border flex items-center gap-2">
            {canWrite ? (
              <>
                <Select value={kind} onValueChange={(v) => setKind(v as InvoiceAttachmentKind)}>
                  <SelectTrigger
                    className="h-8 flex-1 text-xs"
                    aria-label={t('invoices.documents.kindLabel')}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {KINDS.map((k) => (
                      <SelectItem key={k} value={k} className="text-xs">
                        {t(`invoices.documents.kind.${k}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 shrink-0"
                  disabled={uploadMutation.isPending}
                  onClick={() => fileInput.current?.click()}
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  {t('invoices.documents.add')}
                </Button>
                <input
                  ref={fileInput}
                  type="file"
                  className="hidden"
                  data-testid="invoice-document-input"
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    if (file) uploadMutation.mutate(file)
                    event.target.value = ''
                  }}
                />
              </>
            ) : (
              <p className="text-xs font-medium text-muted-foreground py-1">
                {t('invoices.documents.title')}
              </p>
            )}
          </div>

          {error && <p className="px-3 pt-3 text-xs text-destructive">{error}</p>}

          <ul className="flex-1 p-2 space-y-0.5 lg:max-h-[880px] lg:overflow-y-auto">
            {showOurPage && (
              <li>
                <ShelfItem
                  active={active === OUR_PAGE}
                  onSelect={() => setSelected(OUR_PAGE)}
                  icon={<FileText className="h-4 w-4" />}
                  title={ourPageLabel || t('invoices.documents.ourPage')}
                  subtitle={t('invoices.documents.rendered', {
                    date: ourPageDate ? showPlainDate(ourPageDate) : '',
                  })}
                  isPrimary={!anyPrimary}
                />
              </li>
            )}

            {attachments.map((attachment) => (
              <li key={attachment.id}>
                <ShelfItem
                  active={active === attachment.id}
                  onSelect={() => setSelected(attachment.id)}
                  icon={
                    previewKind(attachment.content_type) === 'image' ? (
                      <ImageIcon className="h-4 w-4" />
                    ) : (
                      <FileText className="h-4 w-4" />
                    )
                  }
                  title={attachment.filename}
                  subtitle={[
                    t(`invoices.documents.kind.${attachment.kind}`),
                    formatFileSize(attachment.size),
                    attachment.issued_at ? showPlainDate(attachment.issued_at) : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                  reference={attachment.document_number}
                  provenance={(() => {
                    const from = documentProvenance(attachment.source)
                    const arrived = showArrival(attachment.created_at)
                    return from.kind === 'system'
                      ? t('invoices.documents.fromSystem', { source: from.name, date: arrived })
                      : t('invoices.documents.fromUpload', { date: arrived })
                  })()}
                  isPrimary={attachment.is_primary}
                  actions={
                    canWrite ? (
                      <>
                        {!attachment.is_primary && (
                          <IconAction
                            onClick={() => primaryMutation.mutate(attachment.id)}
                            label={t('invoices.documents.makePrimary')}
                          >
                            <Star className="h-3.5 w-3.5" />
                          </IconAction>
                        )}
                        <IconAction
                          onClick={() => removeMutation.mutate(attachment.id)}
                          label={t('invoices.documents.remove')}
                          destructive
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </IconAction>
                      </>
                    ) : undefined
                  }
                />
              </li>
            ))}

            {!showOurPage && attachments.length === 0 && (
              <li
                className="px-3 py-6 text-xs text-muted-foreground"
                data-testid="invoice-no-documents"
              >
                {t('invoices.documents.empty')}
              </li>
            )}
          </ul>
        </div>

        {/* The reading surface */}
        <div className="flex-1 min-w-0 bg-muted/40 p-3 sm:p-6 overflow-x-auto">
          {active === OUR_PAGE ? (
            ourPage
          ) : activeAttachment ? (
            // Keyed by file: selecting another one remounts rather than
            // reusing a component still holding the previous blob URL.
            <AttachmentView
              key={activeAttachment.id}
              invoiceId={invoiceId}
              attachment={activeAttachment}
            />
          ) : (
            <div className="py-20 text-center" data-testid="invoice-missing-source">
              <FileText className="h-6 w-6 mx-auto text-muted-foreground" />
              <p className="mt-3 text-sm font-medium text-foreground">
                {t('invoices.documents.notOurs')}
              </p>
              <p className="mt-1 text-sm text-muted-foreground max-w-md mx-auto">
                {t('invoices.documents.notOursHint')}
              </p>
            </div>
          )}
        </div>
      </div>
    </SectionCard>
  )
}

function ShelfItem({
  active,
  onSelect,
  icon,
  title,
  subtitle,
  reference,
  provenance,
  isPrimary,
  actions,
}: {
  active: boolean
  onSelect: () => void
  icon: React.ReactNode
  title: string
  subtitle: string
  reference?: string | null
  /** Which system delivered the file and when it arrived. Absent on our
   *  own generated page, which was not delivered by anybody. */
  provenance?: string
  /** True on the file the download hands over. Shown as a filled star —
   *  the same mark the button on the other rows sets, so the flag and the
   *  way to move it read as one thing rather than two. */
  isPrimary?: boolean
  actions?: React.ReactNode
}) {
  const { t } = useTranslation()
  return (
    <div
      data-testid="invoice-document-row"
      className={cn(
        'group flex items-start gap-2 rounded-lg px-2 py-2 transition-colors',
        active ? 'bg-primary/5 ring-1 ring-primary/20' : 'hover:bg-muted',
      )}
    >
      <button onClick={onSelect} className="flex items-start gap-2 min-w-0 flex-1 text-left">
        <span className={cn('mt-0.5 shrink-0', active ? 'text-primary' : 'text-muted-foreground')}>
          {icon}
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-medium text-foreground truncate">{title}</span>
          <span className="block text-xs text-muted-foreground truncate mt-0.5">{subtitle}</span>
          {reference && (
            <span className="block text-[11px] text-muted-foreground/80 truncate mt-0.5 tabular-nums">
              {reference}
            </span>
          )}
          {provenance && (
            <span className="block text-[11px] text-muted-foreground/80 truncate mt-0.5">
              {provenance}
            </span>
          )}
        </span>
      </button>
      {isPrimary && (
        <span
          className="shrink-0 mt-0.5 text-primary"
          title={t('invoices.documents.primaryHint')}
        >
          <Star className="h-3.5 w-3.5 fill-current" />
        </span>
      )}
      {actions && (
        <span className="shrink-0 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
          {actions}
        </span>
      )}
    </div>
  )
}

/** One filed file, rendered where it can be read rather than downloaded. */
function AttachmentView({
  invoiceId,
  attachment,
}: {
  invoiceId: string
  attachment: InvoiceAttachment
}) {
  const { t } = useTranslation()
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let created: string | null = null
    let cancelled = false
    void invoicesApi.attachments.blobUrl(invoiceId, attachment.id).then((next) => {
      if (cancelled) {
        URL.revokeObjectURL(next)
        return
      }
      created = next
      setUrl(next)
    })
    return () => {
      cancelled = true
      if (created) URL.revokeObjectURL(created)
    }
  }, [invoiceId, attachment.id])

  const preview = previewKind(attachment.content_type)

  return (
    <div className="mx-auto max-w-[794px]" data-testid="invoice-source-document">
      <div className="rounded-sm bg-white shadow-[0_1px_2px_rgba(0,0,0,0.08),0_12px_32px_-10px_rgba(0,0,0,0.22)] overflow-hidden">
        {!url ? (
          <div className="h-[560px]" />
        ) : preview === 'pdf' ? (
          // An <iframe>, not an <object>: both hand the file to Chrome's
          // PDF viewer, but the object element is replaced content the
          // plugin resizes past its box, taking the page layout with it.
          <iframe
            src={url}
            title={attachment.filename}
            className="w-full h-[1123px] max-h-[78vh] border-0 block"
          />
        ) : preview === 'image' ? (
          <img src={url} alt={attachment.filename} className="w-full block" />
        ) : (
          <div className="p-10 text-center">
            <p className="text-sm text-muted-foreground">
              {t('invoices.documents.cannotPreview')}
            </p>
          </div>
        )}
      </div>
      {url && (
        <div className="mt-3 text-center">
          <Button size="sm" variant="outline" onClick={() => window.open(url, '_blank', 'noopener')}>
            <FileText className="h-3.5 w-3.5 mr-1.5" />
            {t('invoices.documents.openOriginal')}
          </Button>
        </div>
      )}
    </div>
  )
}
