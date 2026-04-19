import { useState, useEffect, useCallback, useRef } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@/contexts/auth-context'
import { currencies as currenciesApi, transactions as transactionsApi, settings as settingsApi, payees as payeesApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { AlertTriangle, ChevronDown, ChevronLeft, Download, Paperclip, Upload, X, FileText, Plus, Unlink } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { TransactionAttachments } from '@/components/transaction-attachments'
import type { AttachmentPreview } from '@/components/transaction-attachments'
import type { Transaction, RecurringTransaction } from '@/types'
import { toast } from 'sonner'

export type SaveAction = 'save' | 'saveAndNew' | 'saveAndDuplicate'

export function extractApiError(error: unknown): string {
  if (
    error &&
    typeof error === 'object' &&
    'response' in error &&
    error.response &&
    typeof error.response === 'object' &&
    'data' in error.response
  ) {
    const data = (error.response as { data: unknown }).data
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail)) {
        return detail.map((d: { msg?: string; loc?: string[] }) => {
          const field = d.loc?.slice(-1)[0] ?? ''
          return `${field}: ${d.msg ?? 'invalid'}`
        }).join(', ')
      }
    }
  }
  return 'An unexpected error occurred'
}

function isImageType(contentType: string): boolean {
  return contentType.startsWith('image/')
}

export function TransactionDialog({
  open,
  onClose,
  transaction,
  categories,
  accounts,
  recurringMatch,
  onSave,
  onDelete,
  onUnlinkTransfer,
  loading,
  error,
  isSynced = false,
  duplicateDraft = null,
  formResetKey = 0,
}: {
  open: boolean
  onClose: () => void
  transaction: Transaction | null
  categories: { id: string; name: string; icon: string }[]
  accounts: { id: string; name: string }[]
  recurringMatch?: RecurringTransaction
  onSave: (data: Partial<Transaction>, recurringData?: { frequency: string; end_date?: string }, pendingFiles?: File[], action?: SaveAction) => void
  onDelete?: () => void
  onUnlinkTransfer?: (pairId: string) => void
  loading: boolean
  error: string | null
  isSynced?: boolean
  duplicateDraft?: Partial<Transaction> | null
  formResetKey?: number
}) {
  const { t } = useTranslation()
  const [preview, setPreview] = useState<AttachmentPreview | null>(null)

  const handlePreviewChange = useCallback((newPreview: AttachmentPreview | null) => {
    setPreview(prev => {
      if (prev?.url) URL.revokeObjectURL(prev.url)
      return newPreview
    })
  }, [])

  // Clean up preview when dialog closes
  useEffect(() => {
    if (!open) {
      setPreview(prev => {
        if (prev?.url) URL.revokeObjectURL(prev.url)
        return null
      })
    }
  }, [open])

  const handleDownloadPreview = async () => {
    if (!preview || !transaction) return
    try {
      const url = await transactionsApi.attachments.downloadUrl(transaction.id, preview.attachmentId)
      const a = document.createElement('a')
      a.href = url
      a.download = preview.filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch {
      toast.error(t('common.error'))
    }
  }

  const isEditing = !!transaction
  const hasPreview = isEditing && !!preview

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className={cn(
        'transition-[max-width] duration-300',
        hasPreview ? 'sm:max-w-5xl max-w-2xl' : 'sm:max-w-2xl max-w-2xl'
      )}>
        <div className={isEditing ? 'sm:flex sm:gap-0 sm:h-[80vh]' : ''}>
          {/* Left column: form */}
          <div className={isEditing ? 'sm:flex-1 sm:min-w-0 sm:flex sm:flex-col sm:overflow-hidden sm:pr-6' : ''}>
            <DialogHeader className="mb-4">
              <DialogTitle>
                {transaction ? t('common.edit') : t('transactions.addManual')}
              </DialogTitle>
            </DialogHeader>
            <TransactionForm
              key={transaction?.id ?? `new-${formResetKey}`}
              transaction={transaction}
              duplicateDraft={duplicateDraft}
              categories={categories}
              accounts={accounts}
              recurringMatch={recurringMatch}
              onSave={onSave}
              onDelete={onDelete}
              onUnlinkTransfer={onUnlinkTransfer}
              onCancel={onClose}
              loading={loading}
              error={error}
              isSynced={isSynced}
              onPreviewChange={handlePreviewChange}
              activePreviewId={preview?.attachmentId ?? null}
              hasPreview={hasPreview}
            />
          </div>

          {/* Desktop: side panel */}
          <div
            className={cn(
              'hidden sm:flex shrink-0 border-l flex-col overflow-hidden transition-[width] duration-300 ease-in-out',
              hasPreview ? 'w-[420px]' : 'w-0 border-l-0'
            )}
          >
            {preview && (
              <>
                <div className="flex-1 overflow-hidden">
                  {preview.contentType === 'application/pdf' ? (
                    <iframe
                      src={`${preview.url}#toolbar=0&navpanes=0`}
                      title={preview.filename}
                      className="w-full h-full border-0 bg-white"
                    />
                  ) : isImageType(preview.contentType) ? (
                    <div className="flex items-center justify-center h-full p-4 bg-muted/30">
                      <img
                        src={preview.url}
                        alt={preview.filename}
                        className="max-h-full max-w-full rounded object-contain"
                      />
                    </div>
                  ) : null}
                </div>
                <div className="flex items-center gap-2 px-4 py-3 border-t text-sm shrink-0">
                  <button
                    type="button"
                    className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground cursor-pointer"
                    onClick={() => handlePreviewChange(null)}
                    title="Close preview"
                  >
                    <ChevronLeft size={16} />
                  </button>
                  <span className="flex-1 truncate font-medium">{preview.filename}</span>
                  <button
                    type="button"
                    className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground cursor-pointer"
                    onClick={handleDownloadPreview}
                    title="Download"
                  >
                    <Download size={14} />
                  </button>
                </div>
              </>
            )}
          </div>
        </div>

        {/* Mobile: full-screen overlay */}
        {hasPreview && (
          <div className="sm:hidden fixed inset-0 z-[100] bg-background flex flex-col animate-in slide-in-from-right duration-200">
            <div className="flex-1 overflow-hidden">
              {preview.contentType === 'application/pdf' ? (
                <iframe
                  src={`${preview.url}#toolbar=0&navpanes=0`}
                  title={preview.filename}
                  className="w-full h-full border-0 bg-white"
                />
              ) : isImageType(preview.contentType) ? (
                <div className="flex items-center justify-center h-full p-4 bg-muted/30">
                  <img
                    src={preview.url}
                    alt={preview.filename}
                    className="max-h-full max-w-full rounded object-contain"
                  />
                </div>
              ) : null}
            </div>
            <div className="flex items-center gap-2 px-4 py-3 border-t text-sm shrink-0">
              <button
                type="button"
                className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground cursor-pointer"
                onClick={() => handlePreviewChange(null)}
                title="Close preview"
              >
                <ChevronLeft size={18} />
              </button>
              <span className="flex-1 truncate font-medium">{preview.filename}</span>
              <button
                type="button"
                className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground cursor-pointer"
                onClick={handleDownloadPreview}
                title="Download"
              >
                <Download size={16} />
              </button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function TransactionForm({
  transaction,
  duplicateDraft,
  categories,
  accounts,
  recurringMatch,
  onSave,
  onDelete,
  onUnlinkTransfer,
  onCancel,
  loading,
  error,
  isSynced,
  onPreviewChange,
  activePreviewId,
  hasPreview,
}: {
  transaction: Transaction | null
  duplicateDraft: Partial<Transaction> | null
  categories: { id: string; name: string; icon: string }[]
  accounts: { id: string; name: string }[]
  recurringMatch?: RecurringTransaction
  onSave: (data: Partial<Transaction>, recurringData?: { frequency: string; end_date?: string }, pendingFiles?: File[], action?: SaveAction) => void
  onDelete?: () => void
  onUnlinkTransfer?: (pairId: string) => void
  onCancel: () => void
  loading: boolean
  error: string | null
  isSynced: boolean
  onPreviewChange: (preview: AttachmentPreview | null) => void
  activePreviewId: string | null
  hasPreview: boolean
}) {
  const { t, i18n } = useTranslation()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currenciesApi.list,
    staleTime: Infinity,
  })
  const { data: payeesList } = useQuery({
    queryKey: ['payees'],
    queryFn: payeesApi.list,
  })
  const seed = transaction ?? duplicateDraft
  const [description, setDescription] = useState(seed?.description ?? '')
  const [amount, setAmount] = useState(seed?.amount?.toString() ?? '')
  const [date, setDate] = useState(seed?.date ?? new Date().toISOString().split('T')[0])
  const [type, setType] = useState<'debit' | 'credit'>(seed?.type ?? 'debit')
  const [currency, setCurrency] = useState(seed?.currency ?? userCurrency)
  const [categoryId, setCategoryId] = useState(seed?.category_id ?? '')
  const [payeeId, setPayeeId] = useState(seed?.payee_id ?? '')
  const [accountId, setAccountId] = useState(seed?.account_id ?? accounts[0]?.id ?? '')
  const [notes, setNotes] = useState(seed?.notes ?? '')
  const [convertedAmount, setConvertedAmount] = useState(
    seed?.amount_primary != null ? seed.amount_primary.toString() : ''
  )
  const [fxRate, setFxRate] = useState(
    seed?.fx_rate_used != null ? seed.fx_rate_used.toString() : ''
  )
  const [isRecurring, setIsRecurring] = useState(false)
  const [frequency, setFrequency] = useState<'monthly' | 'weekly' | 'yearly'>('monthly')
  const [endDate, setEndDate] = useState('')
  const isCreating = !transaction
  const showConversion = currency !== userCurrency && !isSynced
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [pendingDragOver, setPendingDragOver] = useState(false)
  const pendingFileInputRef = useRef<HTMLInputElement>(null)
  const pendingActionRef = useRef<SaveAction>('save')
  const formRef = useRef<HTMLFormElement>(null)

  const triggerSubmit = (action: SaveAction) => {
    pendingActionRef.current = action
    formRef.current?.requestSubmit()
  }
  const showSaveVariants = isCreating && !isSynced

  const { data: attachmentSettings } = useQuery({
    queryKey: ['settings', 'attachments'],
    queryFn: () => settingsApi.attachments(),
    staleTime: 5 * 60 * 1000,
    enabled: isCreating,
  })
  const allowedExtensions = attachmentSettings?.allowed_extensions ?? ['jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'pdf']
  const maxFileSize = (attachmentSettings?.max_file_size_mb ?? 10) * 1024 * 1024
  const maxAttachments = attachmentSettings?.max_attachments_per_transaction ?? 10

  const addPendingFiles = useCallback((files: FileList | File[]) => {
    const fileArray = Array.from(files)
    setPendingFiles(prev => {
      let current = prev.length
      const next = [...prev]
      for (const file of fileArray) {
        if (current >= maxAttachments) {
          toast.error(t('transactions.attachmentMaxReached'))
          break
        }
        const ext = file.name.includes('.') ? file.name.split('.').pop()!.toLowerCase() : ''
        if (!allowedExtensions.includes(ext)) {
          toast.error(t('transactions.attachmentTypeNotAllowed'))
          continue
        }
        if (file.size > maxFileSize) {
          toast.error(t('transactions.attachmentTooLarge'))
          continue
        }
        next.push(file)
        current++
      }
      return next
    })
  }, [maxAttachments, allowedExtensions, maxFileSize, t])

  const removePendingFile = (index: number) => {
    setPendingFiles(prev => prev.filter((_, i) => i !== index))
  }

  const handleConvertedAmountChange = (val: string) => {
    setConvertedAmount(val)
    const numVal = parseFloat(val)
    const numAmount = parseFloat(amount)
    if (numVal && numAmount) {
      setFxRate((numVal / numAmount).toString())
    } else if (!val) {
      setFxRate('')
    }
  }

  const handleFxRateChange = (val: string) => {
    setFxRate(val)
    const numRate = parseFloat(val)
    const numAmount = parseFloat(amount)
    if (numRate && numAmount) {
      setConvertedAmount((numAmount * numRate).toFixed(2))
    } else if (!val) {
      setConvertedAmount('')
    }
  }

  const handleAmountChange = (val: string) => {
    setAmount(val)
    const numAmount = parseFloat(val)
    const numRate = parseFloat(fxRate)
    if (numRate && numAmount) {
      setConvertedAmount((numAmount * numRate).toFixed(2))
    }
  }

  const handleCurrencyChange = (val: string) => {
    setCurrency(val)
    if (val === userCurrency) {
      setConvertedAmount('')
      setFxRate('')
    }
  }

  return (
    <form
      ref={formRef}
      onSubmit={(e) => {
        e.preventDefault()
        const action = pendingActionRef.current
        pendingActionRef.current = 'save'
        const fxFields: Partial<Transaction> = {}
        if (showConversion && convertedAmount) {
          fxFields.amount_primary = parseFloat(convertedAmount)
        }
        if (showConversion && fxRate) {
          fxFields.fx_rate_used = parseFloat(fxRate)
        }
        const txData = isSynced
          ? {
              category_id: categoryId || null,
              payee_id: payeeId || null,
              notes: notes.trim() || null,
            } as Partial<Transaction>
          : {
              description,
              amount: parseFloat(amount),
              date,
              type,
              currency,
              category_id: categoryId || null,
              payee_id: payeeId || null,
              account_id: accountId || undefined,
              notes: notes.trim() || null,
              ...fxFields,
            } as Partial<Transaction>
        const recurringData = isCreating && isRecurring
          ? { frequency, end_date: endDate || undefined }
          : undefined
        onSave(txData, recurringData, isCreating && pendingFiles.length > 0 ? pendingFiles : undefined, action)
      }}
      className={cn(
        'flex flex-col',
        !isCreating ? 'flex-1 min-h-0' : 'max-h-[85vh]',
        hasPreview && 'mt-4'
      )}
    >
      <div className="space-y-4 overflow-y-auto flex-1 min-h-0 pb-2">
      {error && (
        <div className="p-3 text-sm text-destructive bg-destructive/10 rounded-md">
          {error}
        </div>
      )}
      {isSynced && (
        <div className="flex items-center gap-2 p-3 text-sm bg-amber-50 border border-amber-200 rounded-md text-amber-700">
          {t('transactions.syncedInfo')}
        </div>
      )}
      {!!transaction?.transfer_pair_id && (
        <div className="p-3 text-sm bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800 rounded-md text-blue-700 dark:text-blue-300 space-y-2">
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1 min-w-0">
              <p>{t('transactions.transferInfo')}</p>
              <p className="text-xs text-blue-500 dark:text-blue-400">{t('transactions.transferTooltip')}</p>
            </div>
            {onUnlinkTransfer && transaction?.transfer_pair_id && (
              <button
                type="button"
                disabled={loading}
                onClick={() => {
                  if (transaction?.transfer_pair_id) {
                    onUnlinkTransfer(transaction.transfer_pair_id)
                  }
                }}
                className="shrink-0 inline-flex items-center gap-1.5 rounded-md border border-blue-200 dark:border-blue-800 bg-white/60 dark:bg-blue-900/40 px-2.5 py-1.5 text-xs font-medium text-blue-700 dark:text-blue-200 hover:bg-white dark:hover:bg-blue-900/70 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                title={t('transactions.unlinkTransferConfirm')}
              >
                <Unlink size={12} />
                {t('transactions.unlinkTransfer')}
              </button>
            )}
          </div>
        </div>
      )}
      {recurringMatch && (
        <div className="flex items-center gap-2 p-3 text-sm bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800 rounded-md">
          <span>{t('transactions.recurringInfo', {
            frequency: t(`recurring.${recurringMatch.frequency}`),
            next: new Date(recurringMatch.next_occurrence).toLocaleDateString(locale),
          })}</span>
        </div>
      )}
      <div className="space-y-2">
        <Label>{t('transactions.description')}</Label>
        <Input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          required
          disabled={isSynced}
        />
        {isSynced && transaction?.payee && transaction.payee !== transaction.description && (
          <p className="text-xs text-muted-foreground">{transaction.payee}</p>
        )}
      </div>
      <div className="grid grid-cols-3 gap-4">
        <div className="space-y-2">
          <Label>{t('transactions.amount')}</Label>
          <Input
            type="number"
            step="0.01"
            value={amount}
            onChange={(e) => handleAmountChange(e.target.value)}
            required
            disabled={isSynced}
          />
        </div>
        <div className="space-y-2">
          <Label>{t('transactions.currency')}</Label>
          <select
            className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background h-9 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
            value={currency}
            onChange={(e) => handleCurrencyChange(e.target.value)}
            disabled={isSynced}
          >
            {(supportedCurrencies ?? [{ code: userCurrency, symbol: userCurrency, name: userCurrency, flag: '' }]).map((c) => (
              <option key={c.code} value={c.code}>{c.flag} {c.name}</option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label>{t('transactions.date')}</Label>
          <DatePickerInput
            value={date}
            onChange={setDate}
            disabled={isSynced}
            className="w-full justify-start"
          />
        </div>
      </div>
      {showConversion && (
        <div className="border border-border rounded-md p-3 space-y-2">
          {transaction?.fx_fallback && (
            <div className="flex items-start gap-2 p-2 rounded-md bg-amber-50 dark:bg-amber-950/30 text-amber-700 dark:text-amber-400">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span className="text-xs">{t('transactions.fxFallbackBanner')}</span>
            </div>
          )}
          <div>
            <span className="text-sm font-medium">{t('transactions.conversion')}</span>
            <span className="text-xs text-muted-foreground ml-2">({t('transactions.conversionHint')})</span>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <Label className="text-xs">{t('transactions.convertedAmount', { currency: userCurrency })}</Label>
              <Input
                type="number"
                step="0.01"
                value={convertedAmount}
                onChange={(e) => handleConvertedAmountChange(e.target.value)}
                placeholder={t('transactions.autoCalculated')}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">{t('transactions.exchangeRate')}</Label>
              <Input
                type="number"
                step="0.0001"
                value={fxRate}
                onChange={(e) => handleFxRateChange(e.target.value)}
                placeholder={t('transactions.autoCalculated')}
              />
            </div>
          </div>
        </div>
      )}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>{t('transactions.type')}</Label>
          <select
            className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
            value={type}
            onChange={(e) => setType(e.target.value as 'debit' | 'credit')}
            disabled={isSynced}
          >
            <option value="debit">{t('transactions.expense')}</option>
            <option value="credit">{t('transactions.income')}</option>
          </select>
        </div>
        <div className="space-y-2">
          <Label>{t('transactions.category')}</Label>
          <select
            className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
            value={categoryId}
            onChange={(e) => setCategoryId(e.target.value)}
            disabled={!!transaction?.transfer_pair_id}
          >
            <option value="">{t('transactions.noCategory')}</option>
            {categories.map((cat) => (
              <option key={cat.id} value={cat.id}>{cat.name}</option>
            ))}
          </select>
        </div>
      </div>
      <div className={cn("grid gap-4", isSynced ? "grid-cols-1" : "grid-cols-2")}>
        <div className="space-y-2">
          <Label>{t('payees.payee')}</Label>
          <select
            className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
            value={payeeId}
            onChange={(e) => setPayeeId(e.target.value)}
          >
            <option value="">{t('payees.noPayee')}</option>
            {(payeesList ?? []).map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          {isSynced && transaction?.payee && (
            <p className="text-xs text-muted-foreground">{t('payees.rawPayee')}: {transaction.payee}</p>
          )}
        </div>
        {!isSynced && (
          <div className="space-y-2">
            <Label>{t('transactions.account')}</Label>
            <select
              className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              required
            >
              {accounts.map((acc) => (
                <option key={acc.id} value={acc.id}>{getAccountName(acc)}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="space-y-2">
        <Label>{t('transactions.notes')} <span className="text-muted-foreground font-normal text-xs">({t('transactions.notesHint')})</span></Label>
        <textarea
          className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background resize-none focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-0"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder={t('transactions.notesPlaceholder')}
        />
      </div>

      {!isCreating && transaction ? (
        <TransactionAttachments
          transactionId={transaction.id}
          onPreviewChange={onPreviewChange}
          activePreviewId={activePreviewId}
        />
      ) : isCreating && (
        <PendingAttachmentsSection
          files={pendingFiles}
          dragOver={pendingDragOver}
          maxAttachments={maxAttachments}
          allowedExtensions={allowedExtensions}
          fileInputRef={pendingFileInputRef}
          onDragOver={() => setPendingDragOver(true)}
          onDragLeave={() => setPendingDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setPendingDragOver(false); if (e.dataTransfer.files?.length) addPendingFiles(e.dataTransfer.files) }}
          onFileChange={(e) => { if (e.target.files?.length) { addPendingFiles(e.target.files); e.target.value = '' } }}
          onRemove={removePendingFile}
        />
      )}

      {/* Recurring toggle — only shown when creating non-synced */}
      {isCreating && !isSynced && (
        <div className="space-y-3 border rounded-md p-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={isRecurring}
              onChange={(e) => setIsRecurring(e.target.checked)}
              className="rounded border-gray-300"
            />
            <span className="text-sm font-medium">{t('transactions.makeRecurring')}</span>
          </label>
          {isRecurring && (
            <div className="grid grid-cols-2 gap-4 pt-1">
              <div className="space-y-2">
                <Label>{t('recurring.frequency')}</Label>
                <select
                  className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                  value={frequency}
                  onChange={(e) => setFrequency(e.target.value as 'monthly' | 'weekly' | 'yearly')}
                >
                  <option value="monthly">{t('recurring.monthly')}</option>
                  <option value="weekly">{t('recurring.weekly')}</option>
                  <option value="yearly">{t('recurring.yearly')}</option>
                </select>
              </div>
              <div className="space-y-2">
                <Label>{t('recurring.endDate')}</Label>
                <DatePickerInput
                  value={endDate}
                  onChange={setEndDate}
                  placeholder={t('recurring.endDate')}
                  className="w-full justify-start"
                />
              </div>
            </div>
          )}
        </div>
      )}

      </div>

      <DialogFooter className={cn(
        'shrink-0 border-t pt-4 mt-2',
        onDelete ? 'flex justify-between sm:justify-between' : ''
      )}>
        {onDelete && (
          <Button type="button" variant="destructive" onClick={onDelete} disabled={loading}>
            {t('common.delete')}
          </Button>
        )}
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          {showSaveVariants ? (
            <div className="inline-flex">
              <Button
                type="submit"
                disabled={loading}
                className="rounded-r-none"
              >
                {loading ? t('common.loading') : t('common.save')}
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    disabled={loading}
                    aria-label={t('transactions.moreSaveOptions')}
                    className="rounded-l-none border-l border-l-primary-foreground/20 px-2 has-[>svg]:px-2"
                  >
                    <ChevronDown />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={() => triggerSubmit('saveAndNew')}>
                    {t('transactions.saveAndNew')}
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => triggerSubmit('saveAndDuplicate')}>
                    {t('transactions.saveAndDuplicate')}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          ) : (
            <Button type="submit" disabled={loading}>
              {loading ? t('common.loading') : t('common.save')}
            </Button>
          )}
        </div>
      </DialogFooter>
    </form>
  )
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function PendingAttachmentsSection({
  files,
  dragOver,
  maxAttachments,
  allowedExtensions,
  fileInputRef,
  onDragOver,
  onDragLeave,
  onDrop,
  onFileChange,
  onRemove,
}: {
  files: File[]
  dragOver: boolean
  maxAttachments: number
  allowedExtensions: string[]
  fileInputRef: React.RefObject<HTMLInputElement | null>
  onDragOver: () => void
  onDragLeave: () => void
  onDrop: (e: React.DragEvent) => void
  onFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  onRemove: (index: number) => void
}) {
  const { t } = useTranslation()
  const hasFiles = files.length > 0
  const atMax = files.length >= maxAttachments

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Paperclip size={14} />
        {t('transactions.attachments')}
        {hasFiles && (
          <span className="text-xs text-muted-foreground font-normal">({files.length})</span>
        )}
      </div>

      {hasFiles ? (
        <>
          <div className="grid grid-cols-3 gap-2">
            {files.map((file, index) => {
              const isImg = file.type.startsWith('image/')
              const isPdf = file.type === 'application/pdf'
              const ext = file.name.includes('.') ? file.name.split('.').pop()!.toUpperCase() : 'FILE'

              return (
                <div
                  key={`${file.name}-${index}`}
                  className="group relative rounded-xl overflow-hidden ring-1 ring-border hover:ring-border/80 hover:shadow-md hover:shadow-black/5"
                >
                  <div className="aspect-square bg-muted/50 flex items-center justify-center overflow-hidden relative">
                    {isImg ? (
                      <img
                        src={URL.createObjectURL(file)}
                        alt={file.name}
                        className="w-full h-full object-cover"
                        onLoad={(e) => URL.revokeObjectURL((e.target as HTMLImageElement).src)}
                      />
                    ) : (
                      <div className="flex flex-col items-center gap-2">
                        <div className={`w-12 h-14 rounded-lg flex items-center justify-center ${
                          isPdf ? 'bg-red-500/10' : 'bg-muted'
                        }`}>
                          <FileText size={24} className={isPdf ? 'text-red-500' : 'text-muted-foreground'} />
                        </div>
                        <span className="text-[10px] font-semibold tracking-widest text-muted-foreground/70 uppercase">
                          {ext}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Remove button */}
                  <div className="absolute left-0 right-0 bottom-[44px] flex items-center justify-center gap-1 px-2 py-1.5 opacity-0 translate-y-1 group-hover:opacity-100 group-hover:translate-y-0 transition-all duration-200">
                    <div className="flex items-center gap-1 bg-background/90 dark:bg-card/90 backdrop-blur-sm rounded-lg ring-1 ring-border/50 shadow-lg shadow-black/10 px-1 py-0.5">
                      <button
                        type="button"
                        className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 cursor-pointer transition-colors"
                        onClick={() => onRemove(index)}
                        title={t('common.delete')}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  </div>

                  <div className="px-3 py-2.5 bg-card">
                    <p className="text-[12px] font-medium truncate leading-tight" title={file.name}>
                      {file.name}
                    </p>
                    <p className="text-[10px] text-muted-foreground mt-1 leading-tight">
                      {formatFileSize(file.size)}
                    </p>
                  </div>
                </div>
              )
            })}
          </div>

          {!atMax && (
            <button
              type="button"
              className={`w-full mt-2 rounded-lg border-2 border-dashed py-3 flex items-center justify-center gap-2 cursor-pointer transition-all duration-200 ${
                dragOver
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:border-muted-foreground/40 hover:bg-muted/30'
              }`}
              onDragOver={(e) => { e.preventDefault(); onDragOver() }}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <Plus size={14} className="text-muted-foreground" />
              <span className="text-xs text-muted-foreground">{t('transactions.attachmentsUpload')}</span>
            </button>
          )}
        </>
      ) : (
        <div
          className={`rounded-xl border-2 border-dashed py-6 px-4 text-center transition-all cursor-pointer ${
            dragOver ? 'border-primary bg-primary/5' : 'border-border hover:border-muted-foreground/40'
          }`}
          onDragOver={(e) => { e.preventDefault(); onDragOver() }}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <div className="flex flex-col items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center">
              <Upload size={14} className="text-muted-foreground" />
            </div>
            <span className="text-xs text-muted-foreground">{t('transactions.attachmentsUpload')}</span>
          </div>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={allowedExtensions.map(ext => `.${ext}`).join(',')}
        onChange={onFileChange}
        className="hidden"
      />
    </div>
  )
}
