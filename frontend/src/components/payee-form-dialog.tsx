import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Plus, Trash2, X } from 'lucide-react'

import { fiscal as fiscalApi, payees as payeesApi, type PayeeWritePayload } from '@/lib/api'
import { applyMask, formatTaxId } from '@/lib/tax-id'
import { payeeErrorMessage } from '@/lib/payee-error-message'
import { TaxIdKindPicker } from '@/components/tax-id-kind-picker'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { Payee } from '@/types'

type FormType = '' | 'person' | 'company'

export type PayeeFormDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The payee to edit; null creates a new one. */
  payee: Payee | null
  /** What a new payee starts with, e.g. the name already typed in a search. */
  initialName?: string
  /** Receives the payee that was just created or updated. */
  onSaved: (payee: Payee) => void
  /**
   * When given, the dialog offers Delete for the payee being edited and
   * leaves the confirmation to the caller. Without it there is no Delete:
   * a form opened mid-transaction must not remove a payee.
   */
  onDelete?: (payee: Payee) => void
}

/**
 * Create or edit a payee's whole record: name, legal nature, notes, contact
 * details and fiscal documents. Shared by the payees page and the transaction
 * dialog, so a payee created while booking a transaction is the same record
 * the payees page would have made (issue #1069).
 */
export function PayeeFormDialog({
  open,
  onOpenChange,
  payee,
  initialName = '',
  onSaved,
  onDelete,
}: PayeeFormDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  // '' means the legal nature was not stated, which is the resting state for
  // anything sync created and a legitimate answer, not a missing one.
  const [formName, setFormName] = useState('')
  const [formType, setFormType] = useState<FormType>('')
  const [formNotes, setFormNotes] = useState('')
  const [formEmail, setFormEmail] = useState('')
  const [formPhone, setFormPhone] = useState('')
  const [formAddress, setFormAddress] = useState('')
  const [formWebsite, setFormWebsite] = useState('')
  // Documents this payee has, as ordered rows. A list rather than a slot per
  // possible kind: most cadastros need one document, and a column of empty
  // boxes labelled with documents the user has never heard of reads as a
  // form to fill rather than a fact to record.
  const [taxIdRows, setTaxIdRows] = useState<{ kind: string; value: string }[]>([])

  // Labels, masks and ordering come from the server: the jurisdiction that
  // decides them lives on the workspace, and a second copy of the rule here
  // would drift from it.
  const { data: taxIdMeta } = useQuery({
    queryKey: ['tax-id-kinds'],
    queryFn: fiscalApi.taxIdKinds,
    staleTime: 1000 * 60 * 60,
    // Mounted from the transaction dialog too, where it waits closed on most
    // renders: a workspace's document kinds are only worth asking for once
    // someone opens the form.
    enabled: open,
  })
  const allKinds = taxIdMeta?.kinds ?? []
  const kindOption = (kind: string) => allKinds.find((k) => k.kind === kind)
  // What this jurisdiction asks for, in pack order. Drives which document a
  // new row starts on; the picker itself groups every country.
  const localKinds = allKinds.filter((k) => k.offered)
  const usedKinds = new Set(taxIdRows.map((r) => r.kind))

  const seedTaxIdRows = (editing?: Payee | null): { kind: string; value: string }[] => {
    const existing = (editing?.tax_ids ?? []).map((taxId) => ({
      kind: taxId.kind,
      value: formatTaxId(taxId.value, kindOption(taxId.kind)?.mask ?? null),
    }))
    if (existing.length > 0) return existing
    // A workspace that files somewhere gets its primary document ready to
    // type into. Only when a jurisdiction is set: with none, the only offered
    // kind is the generic one, and a permanently empty "Other document" box
    // is the confusing thing this replaced.
    const primary = taxIdMeta?.jurisdiction ? localKinds[0] : undefined
    return primary ? [{ kind: primary.kind, value: '' }] : []
  }

  // Opening reseeds the form from the payee it was opened on. Adjusted during
  // render rather than in an effect: that is React's own answer for state
  // derived from a changing prop, and it means the first frame already shows
  // the record instead of blank fields.
  const seedKey = open ? (payee?.id ?? `new:${initialName}`) : null
  const [seededKey, setSeededKey] = useState<string | null>(null)
  if (seedKey !== seededKey) {
    setSeededKey(seedKey)
    if (open) {
      setFormName(payee?.name ?? initialName)
      setFormType(payee?.type ?? '')
      setFormNotes(payee?.notes ?? '')
      setFormEmail(payee?.email ?? '')
      setFormPhone(payee?.phone ?? '')
      setFormAddress(payee?.address ?? '')
      setFormWebsite(payee?.website ?? '')
      // Every stored document becomes a row, including kinds this
      // jurisdiction does not ask for: a German VAT number on a Brazilian
      // workspace is a normal state, and hiding it would be worse than
      // showing it.
      setTaxIdRows(seedTaxIdRows(payee))
    }
  }

  // The record the form held when Save was pressed. A save can outlive the
  // form that started it — the user cancels and opens another payee — and its
  // completion must not close that newer form or select the older record.
  const savedKey = useRef<string | null>(null)
  // What a save writes down belongs to this record; only the part of the
  // completion that talks to the form is skipped when the form moved on.
  const stillTheSameForm = () => seedKey === null || seedKey === savedKey.current

  const createMutation = useMutation({
    mutationFn: (data: PayeeWritePayload & { name: string }) => payeesApi.create(data),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      if (!stillTheSameForm()) return
      onOpenChange(false)
      toast.success(t('payees.created'))
      onSaved(created)
    },
    onError: (e: unknown) => toast.error(payeeErrorMessage(e, t) ?? t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: PayeeWritePayload & { id: string }) => payeesApi.update(id, data),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      if (!stillTheSameForm()) return
      onOpenChange(false)
      toast.success(t('payees.updated'))
      onSaved(updated)
    },
    onError: (e: unknown) => toast.error(payeeErrorMessage(e, t) ?? t('common.error')),
  })

  const handleSave = () => {
    const payload: PayeeWritePayload & { name: string } = {
      name: formName,
      // Empty means the legal nature was not stated, which is a value, not a
      // blank to be coerced into one.
      type: formType || null,
      notes: formNotes || undefined,
      email: formEmail.trim() || null,
      phone: formPhone.trim() || null,
      address: formAddress.trim() || null,
      website: formWebsite.trim() || null,
      // An emptied field means "drop this document", so blanks are sent and
      // the server treats them as removals.
      tax_ids: taxIdRows
        .filter((row) => row.value.trim() !== '')
        .map((row) => ({ kind: row.kind, value: row.value })),
    }
    savedKey.current = seedKey
    if (payee) {
      updateMutation.mutate({ id: payee.id, ...payload })
    } else {
      createMutation.mutate(payload)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* The body scrolls, the footer does not: with contact details plus a
          jurisdiction's worth of document fields this form is taller than a
          laptop viewport, and a Save button below the fold is a Save button
          nobody can reach. Mirrors transaction-dialog. */}
      <DialogContent className="sm:max-w-md flex flex-col max-h-[calc(100dvh-2rem)]">
        <DialogHeader>
          <DialogTitle>{payee ? t('payees.edit') : t('payees.add')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 overflow-y-auto flex-1 -mx-1 px-1">
          <div className="space-y-2">
            <Label htmlFor="payee-form-name">{t('payees.name')}</Label>
            <Input
              id="payee-form-name"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="payee-form-type">{t('payees.type')}</Label>
            <select
              id="payee-form-type"
              className="w-full border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
              value={formType}
              onChange={(e) => setFormType(e.target.value as FormType)}
            >
              {/* Unset first, and it is the default: the legal nature
                  only matters once a document is attached, and the
                  document settles it anyway. */}
              <option value="">{t('payees.typeUnset', 'Not specified')}</option>
              <option value="person">{t('payees.typePerson')}</option>
              <option value="company">{t('payees.typeCompany')}</option>
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="payee-form-notes">{t('payees.notes')}</Label>
            <textarea
              id="payee-form-notes"
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
              <Label htmlFor="payee-form-email">{t('payees.email', 'Email')}</Label>
              <Input
                id="payee-form-email"
                type="email"
                value={formEmail}
                onChange={(e) => setFormEmail(e.target.value)}
                placeholder="fin@cliente.com"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="payee-form-phone">{t('payees.phone', 'Phone')}</Label>
              <Input
                id="payee-form-phone"
                value={formPhone}
                onChange={(e) => setFormPhone(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="payee-form-address">{t('payees.address', 'Address')}</Label>
            <Input
              id="payee-form-address"
              value={formAddress}
              onChange={(e) => setFormAddress(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="payee-form-website">{t('payees.website', 'Website')}</Label>
            <Input
              id="payee-form-website"
              value={formWebsite}
              onChange={(e) => setFormWebsite(e.target.value)}
              placeholder="acme.com"
            />
          </div>

          {/* Fiscal documents. Which ones appear comes from the workspace's
              jurisdiction; the rest stay reachable through "other", since a
              counterparty abroad has documents this jurisdiction never
              asks for. */}
          {allKinds.length > 0 && (
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
                      kinds={allKinds}
                      jurisdictions={taxIdMeta?.jurisdictions ?? []}
                      activeJurisdiction={taxIdMeta?.jurisdiction ?? null}
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
                      variant="ghost"
                      size="icon"
                      aria-label={t('common.remove')}
                      onClick={() => setTaxIdRows((prev) => prev.filter((_, i) => i !== index))}
                    >
                      <X size={14} className="text-muted-foreground" />
                    </Button>
                  </div>
                )
              })}
              {usedKinds.size < allKinds.length && (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full"
                  onClick={() => {
                    // Default to the jurisdiction's primary document, which is
                    // the one the overwhelming majority of rows will use.
                    const next =
                      localKinds.find((k) => !usedKinds.has(k.kind)) ??
                      allKinds.find((k) => !usedKinds.has(k.kind))
                    if (next) setTaxIdRows((prev) => [...prev, { kind: next.kind, value: '' }])
                  }}
                >
                  <Plus size={14} className="me-1" />
                  {t('payees.addTaxId', 'Add')}
                </Button>
              )}
            </div>
          )}
        </div>
        <DialogFooter className={payee && onDelete ? 'flex justify-between sm:justify-between' : ''}>
          {payee && onDelete && (
            <Button variant="destructive" onClick={() => onDelete(payee)}>
              <Trash2 size={14} className="me-1" />
              {t('common.delete')}
            </Button>
          )}
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={handleSave}
              disabled={!formName.trim() || createMutation.isPending || updateMutation.isPending}
            >
              {t('common.save')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
