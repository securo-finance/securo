import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Plus, Trash2, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { TaxIdKindPicker } from '@/components/tax-id-kind-picker'
import { fiscal as fiscalApi, payees as payeesApi } from '@/lib/api'
import { applyMask } from '@/lib/tax-id'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { payeeErrorMessage } from '@/lib/payee-errors'
import {
  buildPayeeWritePayload,
  nextTaxIdKind,
  seedTaxIdRows,
  type PayeeFormType,
  type TaxIdRow,
} from '@/lib/payee-form-utils'
import type { Payee, TaxIdKindOption } from '@/types'

export interface PayeeFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** null creates, a payee edits. */
  payee: Payee | null
  /** The row the server returned, after a create or an update. The transaction
   *  editor uses it to select a payee it has just created; the payees page has
   *  no use for it. */
  onSaved?: (payee: Payee) => void
  /** Renders the destructive footer button. Omitted means no Delete — which is
   *  what the transaction editor wants, since deleting the payee you are
   *  half-way through attaching has no confirmation surface there. */
  onRequestDelete?: (payee: Payee) => void
  deletePending?: boolean
  /** Pre-fills the name, so "create" from a search box keeps what was typed. */
  defaultName?: string
}

/** Create or edit one counterparty.
 *
 *  Lived inline in the payees page until the transaction editor needed the
 *  same form: the moment you notice a payee is misnamed is while you are
 *  looking at its transaction, and that was the one place that could not fix
 *  it.
 */
export function PayeeFormDialog({
  open,
  onOpenChange,
  payee,
  onSaved,
  onRequestDelete,
  deletePending,
  defaultName,
}: PayeeFormDialogProps) {
  const { t } = useTranslation()

  // Closing sets `payee` back to null while Radix is still playing the exit
  // animation, which would flip the title and the fields to "create" underneath
  // the fade. Holding what was showing while the dialog was open keeps it
  // still on the way out — and, unlike holding the last non-null payee, it
  // stays correct for a create dialog closing after an edit. Adjusted during
  // render rather than in an effect: that is React's own answer for state
  // derived from a changing prop. Same trick as payee-detail-dialog.
  const [held, setHeld] = useState<Payee | null>(payee)
  if (open && payee !== held) setHeld(payee)
  const shown = open ? payee : held

  // Labels, masks and ordering come from the server: the jurisdiction that
  // decides them lives on the workspace, and a second copy of the rule here
  // would drift from it.
  const { data: taxIdMeta, isPending } = useQuery({
    queryKey: ['tax-id-kinds'],
    queryFn: fiscalApi.taxIdKinds,
    staleTime: 1000 * 60 * 60,
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* The body scrolls, the footer does not: with contact details plus a
          jurisdiction's worth of document fields this form is taller than a
          laptop viewport, and a Save button below the fold is a Save button
          nobody can reach. Mirrors transaction-dialog. */}
      <DialogContent className="sm:max-w-md flex flex-col max-h-[calc(100dvh-2rem)]">
        <DialogHeader>
          <DialogTitle>{shown ? t('payees.edit') : t('payees.add')}</DialogTitle>
        </DialogHeader>
        {isPending ? (
          // Opened from the payees page this query is always warm. Opened from
          // the transaction editor it can be cold, and seeding the document
          // rows before it lands would mount the form with none.
          <div className="space-y-4 py-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : (
          <PayeeForm
            // Re-seeds every field when the dialog is pointed at another payee,
            // which is React's own answer to derived state. Same trick as
            // transaction-dialog.
            key={shown?.id ?? `new:${defaultName ?? ''}`}
            payee={shown}
            defaultName={defaultName}
            kinds={taxIdMeta?.kinds ?? []}
            jurisdiction={taxIdMeta?.jurisdiction ?? null}
            jurisdictions={taxIdMeta?.jurisdictions ?? []}
            onSaved={onSaved}
            onOpenChange={onOpenChange}
            onRequestDelete={onRequestDelete}
            deletePending={deletePending}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

interface PayeeFormProps {
  payee: Payee | null
  defaultName?: string
  kinds: TaxIdKindOption[]
  jurisdiction: string | null
  jurisdictions: { code: string; kinds: string[] }[]
  onSaved?: (payee: Payee) => void
  onOpenChange: (open: boolean) => void
  onRequestDelete?: (payee: Payee) => void
  deletePending?: boolean
}

function PayeeForm({
  payee,
  defaultName,
  kinds,
  jurisdiction,
  jurisdictions,
  onSaved,
  onOpenChange,
  onRequestDelete,
  deletePending,
}: PayeeFormProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [formName, setFormName] = useState(payee?.name ?? defaultName ?? '')
  const [formType, setFormType] = useState<PayeeFormType>(payee?.type ?? '')
  const [formNotes, setFormNotes] = useState(payee?.notes ?? '')
  const [formEmail, setFormEmail] = useState(payee?.email ?? '')
  const [formPhone, setFormPhone] = useState(payee?.phone ?? '')
  const [formAddress, setFormAddress] = useState(payee?.address ?? '')
  const [formWebsite, setFormWebsite] = useState(payee?.website ?? '')
  // Documents this payee has, as ordered rows. A list rather than a slot per
  // possible kind: most cadastros need one document, and a column of empty
  // boxes labelled with documents the user has never heard of reads as a form
  // to fill rather than a fact to record.
  const [taxIdRows, setTaxIdRows] = useState<TaxIdRow[]>(() =>
    seedTaxIdRows(payee, kinds, jurisdiction),
  )

  const kindOption = (kind: string) => kinds.find((k) => k.kind === kind)
  // What this jurisdiction asks for, in pack order. Drives which document a
  // new row starts on; the picker itself groups every country.
  const localKinds = kinds.filter((k) => k.offered)
  const usedKinds = new Set(taxIdRows.map((r) => r.kind))

  const onSuccess = (saved: Payee, message: string) => {
    // Transaction rows render `payee_name`, which the server computes from the
    // payee and caches under ['transactions'] — invalidating only ['payees']
    // leaves the old name on screen everywhere it matters.
    invalidateFinancialQueries(queryClient)
    queryClient.invalidateQueries({ queryKey: ['payees'] })
    toast.success(message)
    // Before the close, so a caller selecting the new payee lands first.
    onSaved?.(saved)
    onOpenChange(false)
  }

  const createMutation = useMutation({
    mutationFn: payeesApi.create,
    onSuccess: (saved) => onSuccess(saved, t('payees.created')),
    onError: (e: unknown) => toast.error(payeeErrorMessage(e, t) ?? t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Parameters<typeof payeesApi.create>[0] & { id: string }) =>
      payeesApi.update(id, data),
    onSuccess: (saved) => onSuccess(saved, t('payees.updated')),
    onError: (e: unknown) => toast.error(payeeErrorMessage(e, t) ?? t('common.error')),
  })

  const handleSave = () => {
    const payload = buildPayeeWritePayload({
      name: formName,
      type: formType,
      notes: formNotes,
      email: formEmail,
      phone: formPhone,
      address: formAddress,
      website: formWebsite,
      taxIdRows,
    })
    if (payee) {
      updateMutation.mutate({ id: payee.id, ...payload })
    } else {
      createMutation.mutate(payload)
    }
  }

  const saving = createMutation.isPending || updateMutation.isPending

  return (
    <>
      {/* Deliberately not a <form>. This subtree is portalled but still a React
          child of the transaction dialog's own <form>, and a submit event would
          bubble through the portal and save the transaction behind the user's
          back. Every button below is type="button" for the same reason —
          ui/button sets no default, so the default is "submit". */}
      <div className="space-y-4 overflow-y-auto flex-1 -mx-1 px-1">
        <div className="space-y-2">
          <Label>{t('payees.name')}</Label>
          <Input
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            onKeyDown={(e) => {
              // Without a <form> there is no implicit submit, and Enter in a
              // one-field-shaped dialog doing nothing reads as broken.
              if (e.key === 'Enter' && formName.trim() && !saving) {
                e.preventDefault()
                handleSave()
              }
            }}
            required
          />
        </div>
        <div className="space-y-2">
          <Label>{t('payees.type')}</Label>
          <select
            className="w-full border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
            value={formType}
            onChange={(e) => setFormType(e.target.value as PayeeFormType)}
          >
            {/* Unset first, and it is the default: the legal nature only
                matters once a document is attached, and the document settles
                it anyway. */}
            <option value="">{t('payees.typeUnset', 'Not specified')}</option>
            <option value="person">{t('payees.typePerson')}</option>
            <option value="company">{t('payees.typeCompany')}</option>
          </select>
        </div>
        <div className="space-y-2">
          <Label>{t('payees.notes')}</Label>
          <textarea
            className="w-full border border-input rounded-md px-3 py-2 text-sm bg-card resize-none focus:outline-none focus:ring-2 focus:ring-ring"
            rows={2}
            value={formNotes}
            onChange={(e) => setFormNotes(e.target.value)}
          />
        </div>

        {/* Contact and billing. Every field optional: most rows here were
            created by sync for a card merchant and will never need any. */}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label>{t('payees.email', 'Email')}</Label>
            <Input
              type="email"
              value={formEmail}
              onChange={(e) => setFormEmail(e.target.value)}
              placeholder="fin@cliente.com"
            />
          </div>
          <div className="space-y-2">
            <Label>{t('payees.phone', 'Phone')}</Label>
            <Input value={formPhone} onChange={(e) => setFormPhone(e.target.value)} />
          </div>
        </div>
        <div className="space-y-2">
          <Label>{t('payees.address', 'Address')}</Label>
          <Input value={formAddress} onChange={(e) => setFormAddress(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label>{t('payees.website', 'Website')}</Label>
          <Input
            value={formWebsite}
            onChange={(e) => setFormWebsite(e.target.value)}
            placeholder="acme.com"
          />
        </div>

        {/* Fiscal documents. Which ones appear comes from the workspace's
            jurisdiction; the rest stay reachable through "other", since a
            counterparty abroad has documents this jurisdiction never asks
            for. */}
        {kinds.length > 0 && (
          <div className="space-y-2">
            <Label>{t('payees.taxIds', 'Tax IDs')}</Label>
            {taxIdRows.length === 0 && (
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                {t('payees.taxIdsEmpty', 'None yet. Add one if you need it for tax purposes.')}
              </p>
            )}
            {taxIdRows.map((row, index) => {
              const option = kindOption(row.kind)
              return (
                <div key={index} className="flex items-center gap-2">
                  <TaxIdKindPicker
                    kinds={kinds}
                    jurisdictions={jurisdictions}
                    activeJurisdiction={jurisdiction}
                    value={row.kind}
                    documentValue={row.value}
                    used={usedKinds}
                    onChange={(kind) =>
                      setTaxIdRows((prev) =>
                        prev.map((r, i) =>
                          i === index
                            ? // Re-mask under the new kind: what the user typed
                              // for a CNPJ is not formatted like a VAT id.
                              { kind, value: applyMask(r.value, kindOption(kind)?.mask ?? null) }
                            : r,
                        ),
                      )
                    }
                  />
                  <Input
                    value={row.value}
                    onChange={(e) =>
                      setTaxIdRows((prev) =>
                        prev.map((r, i) =>
                          i === index
                            ? { ...r, value: applyMask(e.target.value, option?.mask ?? null) }
                            : r,
                        ),
                      )
                    }
                    placeholder={option?.mask ?? ''}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={t('common.remove', 'Remove')}
                    onClick={() => setTaxIdRows((prev) => prev.filter((_, i) => i !== index))}
                  >
                    <X size={14} className="text-muted-foreground" />
                  </Button>
                </div>
              )
            })}
            {usedKinds.size < kinds.length && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => {
                  const next = nextTaxIdKind(kinds, localKinds, usedKinds)
                  if (next) setTaxIdRows((prev) => [...prev, { kind: next.kind, value: '' }])
                }}
              >
                <Plus size={14} className="mr-1" />
                {t('payees.addTaxId', 'Add')}
              </Button>
            )}
          </div>
        )}
      </div>
      <DialogFooter className={payee && onRequestDelete ? 'flex justify-between sm:justify-between' : ''}>
        {payee && onRequestDelete && (
          <Button
            type="button"
            variant="destructive"
            onClick={() => onRequestDelete(payee)}
            disabled={deletePending}
          >
            <Trash2 size={14} className="mr-1" />
            {t('common.delete')}
          </Button>
        )}
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button type="button" onClick={handleSave} disabled={!formName.trim() || saving}>
            {t('common.save')}
          </Button>
        </div>
      </DialogFooter>
    </>
  )
}
