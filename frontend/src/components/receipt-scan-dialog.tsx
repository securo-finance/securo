import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle, Camera, Link2, Paperclip, ScanText } from 'lucide-react'
import { accounts as accountsApi, categories as categoriesApi, categoryGroups as categoryGroupsApi, documentAi } from '@/lib/api'
import { getAccountName, sortAccountsByDisplayName } from '@/lib/account-utils'
import { formatCurrency } from '@/lib/format'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import {
  DOCUMENT_EXTENSIONS,
  documentErrorMessage,
  errorFromResponse,
  isImageFile,
  useDocumentAiStatus,
  useDocumentExtraction,
} from '@/hooks/use-document-extraction'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { CategorySelect } from '@/components/category-select'
import type { DocumentExtraction } from '@/types'

type Draft = {
  description: string
  amount: string
  date: string
  type: 'debit' | 'credit'
  currency: string
  accountId: string
  categoryId: string
}

function draftFrom(extraction: DocumentExtraction): Draft {
  const receipt = extraction.receipt
  return {
    description: receipt?.merchant ?? '',
    amount: receipt?.total ?? '',
    date: receipt?.date ?? new Date().toISOString().slice(0, 10),
    type: receipt?.kind === 'refund' ? 'credit' : 'debit',
    currency: receipt?.currency ?? '',
    // No account up front: the first one in the list can be a loan, and a
    // receipt booked there unnoticed costs more than one more pick.
    accountId: '',
    categoryId: extraction.suggested_category_id ?? '',
  }
}

/**
 * Read a receipt (photo or PDF), then attach it to the transaction it pays
 * for or create one from it. Nothing is saved until one of the two is chosen.
 */
export function ReceiptScanDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const cameraRef = useRef<HTMLInputElement>(null)
  const { status } = useDocumentAiStatus()
  const doc = useDocumentExtraction('receipt')
  const extraction = doc.extraction
  const [draft, setDraft] = useState<Draft | null>(null)
  const [draftFor, setDraftFor] = useState<string | null>(null)

  const { data: accountsList = [] } = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list(), enabled: open })
  const { data: categoriesList = [] } = useQuery({ queryKey: ['categories'], queryFn: categoriesApi.list, enabled: open })
  const { data: groupsList = [] } = useQuery({ queryKey: ['category-groups'], queryFn: categoryGroupsApi.list, enabled: open })
  const openAccounts = sortAccountsByDisplayName(accountsList.filter(a => !a.is_closed))

  // Seed the form once per read, during render (no effect round trip).
  if (extraction?.status === 'done' && draftFor !== extraction.id) {
    setDraftFor(extraction.id)
    setDraft(draftFrom(extraction))
  }

  useEffect(() => {
    if (doc.uploadError) toast.error(errorFromResponse(t, doc.uploadError))
  }, [doc.uploadError, t])

  const close = () => {
    doc.reset()
    setDraft(null)
    setDraftFor(null)
    onOpenChange(false)
  }

  const done = (message: string, level: 'success' | 'warning' = 'success') => {
    invalidateFinancialQueries(queryClient)
    queryClient.invalidateQueries({ queryKey: ['attachments'] })
    toast[level](message)
    close()
  }

  const attach = useMutation({
    mutationFn: (transactionId: string) => documentAi.attach(extraction!.id, transactionId),
    onSuccess: () => done(t('documentAi.receipt.attached')),
    onError: (error) => toast.error(errorFromResponse(t, error)),
  })

  const create = useMutation({
    mutationFn: () => documentAi.createTransaction(extraction!.id, {
      account_id: draft!.accountId,
      description: draft!.description.trim(),
      amount: Number(draft!.amount),
      date: draft!.date,
      type: draft!.type,
      currency: draft!.currency || null,
      category_id: draft!.categoryId || null,
    }),
    onSuccess: (result) => {
      // The transaction exists either way; when its file did not make it,
      // say only that (it can be attached from the transaction itself).
      if (result.attached === false) done(t('documentAi.receipt.createdWithoutFile'), 'warning')
      else done(t('documentAi.receipt.created'))
    },
    onError: (error) => toast.error(errorFromResponse(t, error)),
  })

  const pick = (file: File | undefined) => {
    if (!file) return
    if (isImageFile(file.name) && status?.vision === 'no') {
      toast.error(t('documentAi.errors.vision_required'))
      return
    }
    doc.start(file)
  }

  const canCreate = !!draft && !!draft.accountId && !!draft.description.trim() && Number(draft.amount) > 0 && !!draft.date
  const busy = attach.isPending || create.isPending
  const receipt = extraction?.receipt

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      {/* One column that can shrink: with the grid's implicit auto column, the
          account select's longest option and the match rows set the width
          and push the dialog past a phone screen. Capped at the screen height
          and scrolling, so the title, close button and footer stay reachable
          when a read brings several matches. */}
      <DialogContent className="max-h-[calc(100dvh-2rem)] grid-cols-[minmax(0,1fr)] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('documentAi.receipt.title')}</DialogTitle>
          <DialogDescription>{t('documentAi.receipt.description')}</DialogDescription>
        </DialogHeader>

        <input
          ref={fileRef}
          type="file"
          className="hidden"
          accept={DOCUMENT_EXTENSIONS.join(',')}
          onChange={(e) => { pick(e.target.files?.[0]); e.target.value = '' }}
        />
        <input
          ref={cameraRef}
          type="file"
          className="hidden"
          accept="image/*"
          capture="environment"
          onChange={(e) => { pick(e.target.files?.[0]); e.target.value = '' }}
        />

        {status && status.vision === 'no' && !extraction && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
            <AlertCircle size={14} className="mt-px shrink-0" />
            <span>{t('documentAi.textOnlyModel', { model: status.model ?? '' })}</span>
          </div>
        )}

        {!extraction && !doc.reading && (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Button variant="outline" className="h-20 flex-col gap-1.5" onClick={() => fileRef.current?.click()}>
              <Paperclip size={18} />
              <span className="text-xs">{t('documentAi.receipt.chooseFile')}</span>
            </Button>
            <Button
              variant="outline"
              className="h-20 flex-col gap-1.5"
              disabled={status?.vision === 'no'}
              onClick={() => cameraRef.current?.click()}
            >
              <Camera size={18} />
              <span className="text-xs">{t('documentAi.receipt.takePhoto')}</span>
            </Button>
          </div>
        )}

        {doc.reading && (
          <div className="flex flex-col items-center py-8 text-center">
            <div className="mb-3 flex h-12 w-12 animate-pulse items-center justify-center rounded-full bg-primary/10">
              <ScanText size={22} className="text-primary" />
            </div>
            <p className="text-sm font-semibold text-foreground">{t('documentAi.reading')}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('documentAi.readingHint', { model: extraction?.model ?? status?.model ?? '' })}
            </p>
          </div>
        )}

        {extraction?.status === 'failed' && (
          <div className="space-y-3 py-2">
            <div className="flex items-start gap-2 text-sm text-rose-600 dark:text-rose-400">
              <AlertCircle size={16} className="mt-0.5 shrink-0" />
              <span>{documentErrorMessage(t, extraction.error_code, extraction.error)}</span>
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={doc.retrying} onClick={() => doc.retry()}>{t('documentAi.retry')}</Button>
              <Button size="sm" variant="ghost" onClick={() => { doc.reset(); setDraft(null); setDraftFor(null) }}>
                {t('documentAi.receipt.another')}
              </Button>
            </div>
          </div>
        )}

        {extraction?.status === 'done' && draft && (
          <div className="space-y-4" data-testid="receipt-result">
            {extraction.unread_pages.length > 0 && (
              <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                <AlertCircle size={14} className="mt-px shrink-0" />
                <span>{t('documentAi.unreadPages', { pages: extraction.unread_pages.join(', ') })}</span>
              </div>
            )}
            {extraction.matches.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium text-muted-foreground">{t('documentAi.receipt.matchesTitle')}</p>
                <ul className="divide-y divide-border rounded-lg border border-border">
                  {extraction.matches.map(match => (
                    <li key={match.transaction_id} className="flex items-center gap-3 px-3 py-2">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm text-foreground">{match.description}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {match.date} · {match.account_name}
                          {match.attachments > 0 && ` · ${t('documentAi.receipt.hasAttachments', { count: match.attachments })}`}
                        </p>
                      </div>
                      <span className="shrink-0 text-sm tabular-nums text-foreground">
                        {formatCurrency(Number(match.amount), match.currency, locale)}
                      </span>
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => attach.mutate(match.transaction_id)}>
                        <Link2 size={14} className="me-1" />{t('documentAi.receipt.attach')}
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="space-y-3">
              <p className="text-xs font-medium text-muted-foreground">
                {extraction.matches.length > 0 ? t('documentAi.receipt.orCreate') : t('documentAi.receipt.noMatches')}
              </p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-1 sm:col-span-2">
                  <Label htmlFor="receipt-description">{t('transactions.description')}</Label>
                  <Input id="receipt-description" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="receipt-amount">{t('transactions.amount')}</Label>
                  <Input id="receipt-amount" inputMode="decimal" value={draft.amount} onChange={(e) => setDraft({ ...draft, amount: e.target.value.replace(',', '.') })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="receipt-date">{t('transactions.date')}</Label>
                  <Input id="receipt-date" type="date" value={draft.date} onChange={(e) => setDraft({ ...draft, date: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="receipt-account">{t('transactions.account')}</Label>
                  <select
                    id="receipt-account"
                    className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm focus:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/30"
                    value={draft.accountId}
                    onChange={(e) => setDraft({ ...draft, accountId: e.target.value })}
                  >
                    <option value="">{t('import.selectAccount')}</option>
                    {openAccounts.map(acc => <option key={acc.id} value={acc.id}>{getAccountName(acc)}</option>)}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label id="receipt-category-label">{t('transactions.category')}</Label>
                  <CategorySelect
                    value={draft.categoryId}
                    onChange={(value) => setDraft({ ...draft, categoryId: value })}
                    categories={categoriesList}
                    groups={groupsList}
                    allowNone
                    labelledBy="receipt-category-label"
                  />
                </div>
              </div>
              {receipt && (receipt.merchant_tax_id || receipt.payment_method) && (
                <p className="text-xs text-muted-foreground">
                  {[receipt.merchant_tax_id, receipt.payment_method].filter(Boolean).join(' · ')}
                </p>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={close}>{t('common.cancel')}</Button>
          {extraction?.status === 'done' && draft && (
            <Button disabled={!canCreate || busy} onClick={() => create.mutate()}>
              {t('documentAi.receipt.create')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
