import { useState, useRef, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAccountName, sortAccountsByDisplayName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { transactions as transactionsApi, accounts as accountsApi, categories as categoriesApi, categoryGroups as categoryGroupsApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import type { ImportPreviewTransaction, ImportReviewTransaction, FailedRow } from '@/types'
import { Upload, FileText, X, CheckCircle2, AlertCircle, Settings2, Download } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { PageHeader } from '@/components/page-header'
import { AssetImportPanel } from '@/components/asset-import-panel'
import { ImportSummaryBar } from '@/components/import-summary-bar'
import { ImportReviewTable } from '@/components/import-review-table'
import { ImportHistory } from '@/components/import-history'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'

const TYPE_LABELS: Record<string, string> = {
  checking: 'accounts.typeChecking',
  savings: 'accounts.typeSavings',
  credit_card: 'accounts.typeCreditCard',
  investment: 'accounts.typeInvestment',
}

// Securo fields a CSV column can be mapped to, in display order.
const CSV_MAPPING_FIELDS = [
  { key: 'date', label: 'import.mapDate' },
  { key: 'description', label: 'import.mapDescription' },
  { key: 'amount', label: 'import.mapAmount' },
  { key: 'type', label: 'import.mapType' },
  { key: 'category', label: 'import.mapCategory' },
  { key: 'currency', label: 'import.mapCurrency' },
  { key: 'fx_rate', label: 'import.mapFxRate' },
  { key: 'payee', label: 'import.mapPayee' },
  { key: 'external_id', label: 'import.mapExternalId' },
  { key: 'notes', label: 'import.mapNotes' },
] as const

function toReviewTransactions(txns: ImportPreviewTransaction[]): ImportReviewTransaction[] {
  return txns.map((tx, i) => ({
    ...tx,
    _id: tx.external_id ? `${tx.external_id}-${i}` : `idx-${i}`,
    excluded: false,
    selected_category_id: undefined,
  }))
}

function TransactionImportPanel() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [previewData, setPreviewData] = useState<{ transactions: ImportPreviewTransaction[]; detected_format: string; csv_columns?: string[]; parse_error?: string | null; failed_rows?: FailedRow[] } | null>(null)
  const [reviewTransactions, setReviewTransactions] = useState<ImportReviewTransaction[]>([])
  const [selectedAccount, setSelectedAccount] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [fileName, setFileName] = useState<string | null>(null)
  const [currentFile, setCurrentFile] = useState<File | null>(null)
  const [csvHeaders, setCsvHeaders] = useState<string[]>([])
  const [isFailedRowsOpen, setIsFailedRowsOpen] = useState(false)

  const [searchQuery, setSearchQuery] = useState('')
  const [filterCategoryIds, setFilterCategoryIds] = useState<string[]>([])
  const [filterUncategorized, setFilterUncategorized] = useState(false)
  const [statusFilter, setStatusFilter] = useState<'all' | 'included' | 'excluded'>('all')
  const [currentPage, setCurrentPage] = useState(1)

  const [csvDateFormat, setCsvDateFormat] = useState('')
  const [csvFlipAmount, setCsvFlipAmount] = useState(false)
  const [csvDetectDuplicates, setCsvDetectDuplicates] = useState(true)
  const [csvSplitColumns, setCsvSplitColumns] = useState(false)
  const [csvInflowColumn, setCsvInflowColumn] = useState('')
  const [csvOutflowColumn, setCsvOutflowColumn] = useState('')
  const [csvColumnMapping, setCsvColumnMapping] = useState<Record<string, string>>({})

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const { data: categoriesList = [] } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: categoryGroupsList = [] } = useQuery({
    queryKey: ['category-groups'],
    queryFn: categoryGroupsApi.list,
  })

  const previewMutation = useMutation({
    mutationFn: ({ file, options }: { file: File; options?: { date_format?: string; flip_amount?: boolean; inflow_column?: string; outflow_column?: string; column_mapping?: Record<string, string> } }) =>
      transactionsApi.previewImport(file, options),
    onSuccess: (data) => {
      setPreviewData(data)
      setCsvHeaders(data.csv_columns ?? [])
      setReviewTransactions(toReviewTransactions(data.transactions))
      setSearchQuery('')
      setFilterCategoryIds([])
      setFilterUncategorized(false)
      setStatusFilter('all')
      setCurrentPage(1)
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail || t('import.processError'))
    },
  })

  const importMutation = useMutation({
    mutationFn: () => {
      const txns = reviewTransactions.map(rt => ({
        description: rt.description,
        amount: rt.amount,
        date: rt.date,
        type: rt.type,
        external_id: rt.external_id ?? undefined,
        currency: rt.currency ?? undefined,
        fx_rate: rt.fx_rate ?? undefined,
        payee_raw: rt.payee_raw ?? undefined,
        notes: rt.notes ?? undefined,
        category_name: rt.category_name ?? undefined,
        excluded: rt.excluded,
        category_id: rt.selected_category_id !== undefined
          ? (rt.selected_category_id ?? undefined)
          : (rt.suggested_category_id ?? undefined),
        force_uncategorized: rt.selected_category_id === null,
      }))
      return transactionsApi.import(
        selectedAccount,
        txns,
        fileName ?? '',
        previewData!.detected_format,
        isCsvFile ? { detect_duplicates: csvDetectDuplicates } : undefined,
      )
    },
    onSuccess: (data) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['import-logs'] })
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      queryClient.invalidateQueries({ queryKey: ['categories'] })
      const hasSkippedOrExcluded = (data.skipped ?? 0) > 0 || (data.excluded ?? 0) > 0
      const msg = hasSkippedOrExcluded
        ? t('import.importedWithExcluded', { imported: data.imported, skipped: data.skipped ?? 0, excluded: data.excluded ?? 0 })
        : `${data.imported} ${t('import.transactionsImported')}`
      toast.success(msg)
      setPreviewData(null)
      setReviewTransactions([])
      setSelectedAccount('')
      setFileName(null)
      setCurrentFile(null)
      resetCsvOptions()
      if (fileInputRef.current) fileInputRef.current.value = ''
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail || t('import.importError'))
    },
  })

  function resetCsvOptions() {
    setCsvDateFormat('')
    setCsvFlipAmount(false)
    setCsvDetectDuplicates(true)
    setCsvSplitColumns(false)
    setCsvInflowColumn('')
    setCsvOutflowColumn('')
    setCsvColumnMapping({})
    setCsvHeaders([])
  }

  function processFile(file: File) {
    setFileName(file.name)
    setCurrentFile(file)
    resetCsvOptions()
    // CSV headers come back from the preview response (csv_columns), which
    // parses the file server-side and handles any delimiter/quoting.
    previewMutation.mutate({ file })
  }

  // Re-run the preview with the current CSV options. Accepts overrides so a
  // change handler can pass its new value synchronously instead of waiting
  // for the corresponding state update to flush.
  const rePreview = useCallback((overrides?: {
    date_format?: string
    flip_amount?: boolean
    split?: boolean
    inflow?: string
    outflow?: string
    mapping?: Record<string, string>
  }) => {
    if (!currentFile) return
    const dateFormat = overrides?.date_format ?? csvDateFormat
    const flip = overrides?.flip_amount ?? csvFlipAmount
    const split = overrides?.split ?? csvSplitColumns
    const inflow = overrides?.inflow ?? csvInflowColumn
    const outflow = overrides?.outflow ?? csvOutflowColumn
    const mapping = overrides?.mapping ?? csvColumnMapping

    const options: { date_format?: string; flip_amount?: boolean; inflow_column?: string; outflow_column?: string; column_mapping?: Record<string, string> } = {}
    if (dateFormat) options.date_format = dateFormat
    if (flip) options.flip_amount = true
    if (split && inflow && outflow) {
      options.inflow_column = inflow
      options.outflow_column = outflow
    }
    const cleanMapping = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v))
    if (Object.keys(cleanMapping).length > 0) options.column_mapping = cleanMapping
    previewMutation.mutate({ file: currentFile, options })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFile, csvDateFormat, csvFlipAmount, csvSplitColumns, csvInflowColumn, csvOutflowColumn, csvColumnMapping])

  const handleMappingChange = useCallback((field: string, column: string) => {
    setCsvColumnMapping(prev => {
      const next = { ...prev, [field]: column }
      rePreview({ mapping: next })
      return next
    })
  }, [rePreview])

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) processFile(file)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files?.[0]
    if (file) processFile(file)
  }

  const handleReset = () => {
    setPreviewData(null)
    setReviewTransactions([])
    setFileName(null)
    setCurrentFile(null)
    setSelectedAccount('')
    resetCsvOptions()
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleToggleExcluded = useCallback((id: string) => {
    setReviewTransactions(prev => prev.map(t =>
      t._id === id ? { ...t, excluded: !t.excluded } : t
    ))
  }, [])

  const handleChangeCategory = useCallback((id: string, categoryId: string | null) => {
    setReviewTransactions(prev => prev.map(t =>
      t._id === id ? { ...t, selected_category_id: categoryId } : t
    ))
  }, [])

  const isCsvFile = fileName?.toLowerCase().endsWith('.csv') ?? false
  // QIF dates are ambiguous for days 1-12 (DD/MM vs MM/DD), so the file
  // options panel is shown for QIF too, limited to the date-format selector.
  const isQifFile = fileName?.toLowerCase().endsWith('.qif') ?? false

  const incomeCount = previewData?.transactions.filter(t => t.type === 'credit').length ?? 0
  const expenseCount = previewData?.transactions.filter(t => t.type === 'debit').length ?? 0

  const includedCount = reviewTransactions.filter(t => !t.excluded).length

  return (
    <div className="space-y-6">
      {/* Upload zone */}
      {canWrite && <div
        className={`bg-card rounded-xl border-2 border-dashed transition-all cursor-pointer ${
          dragOver ? 'border-primary bg-primary/5' : 'border-border hover:border-border'
        }`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => !previewMutation.isPending && fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".ofx,.qfx,.csv,.qif,.xml,.camt"
          onChange={handleFileChange}
          className="hidden"
        />

        <div className="flex flex-col items-center justify-center py-12 px-6 text-center">
          {previewMutation.isPending ? (
            <>
              <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center mb-4 animate-pulse">
                <FileText size={22} className="text-primary" />
              </div>
              <p className="text-sm font-semibold text-foreground">{t('import.processing')}</p>
              <p className="text-xs text-muted-foreground mt-1">{fileName}</p>
            </>
          ) : fileName && previewData ? (
            <>
              <div className="w-12 h-12 rounded-full bg-emerald-100 flex items-center justify-center mb-4">
                <CheckCircle2 size={22} className="text-emerald-500" />
              </div>
              <p className="text-sm font-semibold text-foreground">{fileName}</p>
              <p className="text-xs text-muted-foreground mt-1">
                {t('import.previewInfo', { count: previewData.transactions.length, format: previewData.detected_format.toUpperCase() })}
              </p>
              <button
                className="mt-3 text-xs text-muted-foreground hover:text-rose-500 transition-colors flex items-center gap-1"
                onClick={(e) => { e.stopPropagation(); handleReset() }}
              >
                <X size={12} /> {t('import.removeFile')}
              </button>
            </>
          ) : (
            <>
              <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center mb-4">
                <Upload size={22} className="text-muted-foreground" />
              </div>
              <p className="text-sm font-semibold text-foreground mb-1">
                {t('import.dragOrClick')}
              </p>
              <p className="text-xs text-muted-foreground">{t('import.acceptedFormats')}</p>
              <button
                className="mt-2 text-xs text-primary hover:text-primary/80 transition-colors flex items-center gap-1"
                onClick={(e) => {
                  e.stopPropagation()
                  const csv = 'date,description,amount,currency,fx_rate\n2026-01-15,Grocery Store,-120.50,USD,\n2026-01-20,Salary Payment,5000.00,EUR,1.08\n'
                  const blob = new Blob([csv], { type: 'text/csv' })
                  const url = URL.createObjectURL(blob)
                  const a = document.createElement('a')
                  a.href = url
                  a.download = 'template.csv'
                  a.click()
                  URL.revokeObjectURL(url)
                }}
              >
                <Download size={12} />
                {t('import.downloadTemplate')}
              </button>
            </>
          )}
        </div>
      </div>}

      {/* Review section */}
      {previewData && (
        <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
          {/* Header */}
          <div className="px-5 py-4 border-b border-border">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-foreground">{t('import.preview')}</p>
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span className="flex items-center gap-1 text-emerald-600">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
                  {t('import.incomeCount', { count: incomeCount })}
                </span>
                <span className="flex items-center gap-1 text-rose-500">
                  <span className="w-1.5 h-1.5 rounded-full bg-rose-500 inline-block" />
                  {t('import.expenseCount', { count: expenseCount })}
                </span>
              </div>
            </div>
          </div>

          {/* Account picker */}
          <div className="px-4 sm:px-5 py-4 border-b border-border bg-muted/50">
            <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4">
              <Label className="text-sm text-muted-foreground whitespace-nowrap shrink-0">
                {t('import.importTo')}
              </Label>
              <select
                className="flex-1 border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                value={selectedAccount}
                onChange={(e) => setSelectedAccount(e.target.value)}
              >
                <option value="">{t('import.selectAccount')}</option>
                {sortAccountsByDisplayName(accountsList ?? []).map((acc) => (
                  <option key={acc.id} value={acc.id}>{getAccountName(acc)} ({t(TYPE_LABELS[acc.type] || acc.type)})</option>
                ))}
              </select>
              {!selectedAccount && (
                <div className="flex items-center gap-1.5 text-xs text-amber-600 bg-amber-50 border border-amber-100 px-2.5 py-1.5 rounded-lg shrink-0">
                  <AlertCircle size={12} />
                  {t('import.selectAccountWarning')}
                </div>
              )}
            </div>
          </div>

          {/* CSV/QIF Options */}
          {(isCsvFile || isQifFile) && previewData && (
            <div className="px-5 py-4 border-b border-border bg-muted/30">
              <div className="flex items-center gap-2 mb-3">
                <Settings2 size={14} className="text-muted-foreground" />
                <p className="text-xs font-medium text-muted-foreground">
                  {isCsvFile ? t('import.csvOptions') : t('import.importOptions')}
                </p>
              </div>

              {previewData.parse_error && (
                <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 px-3 py-2 rounded-lg mb-3">
                  <AlertCircle size={14} className="shrink-0 mt-0.5" />
                  <span>{t('import.mappingNeeded')}</span>
                </div>
              )}

              {previewData.failed_rows && previewData.failed_rows.length > 0 && (
                <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 px-3 py-2 rounded-lg mb-3">
                  <AlertCircle size={14} className="shrink-0 mt-0.5" />
                  <div className="flex-1">
                    <span>
                      {t('import.failedRowsWarning', { count: previewData.failed_rows.length })}
                    </span>
                    <button
                      type="button"
                      className="ml-2 font-semibold underline hover:text-amber-800 focus:outline-none"
                      onClick={() => setIsFailedRowsOpen(true)}
                    >
                      {t('import.viewDetails')}
                    </button>
                  </div>
                </div>
              )}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                <div>
                  <Label className="text-xs text-muted-foreground mb-1 block">{t('import.dateFormat')}</Label>
                  <select
                    className="w-full border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                    value={csvDateFormat}
                    onChange={(e) => { setCsvDateFormat(e.target.value); rePreview({ date_format: e.target.value }) }}
                  >
                    <option value="">{t('import.dateFormatAuto')}</option>
                    <option value="DD/MM/YYYY">DD/MM/YYYY</option>
                    <option value="MM/DD/YYYY">MM/DD/YYYY</option>
                    <option value="YYYY-MM-DD">YYYY-MM-DD</option>
                  </select>
                </div>
                {isCsvFile && <div className="flex items-center gap-2 pt-4">
                  <input
                    type="checkbox"
                    id="flip-amount"
                    checked={csvFlipAmount}
                    onChange={(e) => { setCsvFlipAmount(e.target.checked); rePreview({ flip_amount: e.target.checked }) }}
                    className="rounded border-border text-primary focus:ring-primary"
                  />
                  <Label htmlFor="flip-amount" className="text-sm text-muted-foreground cursor-pointer">
                    {t('import.flipAmounts')}
                  </Label>
                </div>}
                {isCsvFile && <div className="flex items-center gap-2 pt-4">
                  <input
                    type="checkbox"
                    id="split-columns"
                    checked={csvSplitColumns}
                    onChange={(e) => { setCsvSplitColumns(e.target.checked); rePreview({ split: e.target.checked }) }}
                    className="rounded border-border text-primary focus:ring-primary"
                  />
                  <Label htmlFor="split-columns" className="text-sm text-muted-foreground cursor-pointer">
                    {t('import.splitColumns')}
                  </Label>
                </div>}
                {isCsvFile && <div className="flex items-center gap-2 pt-4">
                  <input
                    type="checkbox"
                    id="detect-duplicates"
                    checked={csvDetectDuplicates}
                    onChange={(e) => setCsvDetectDuplicates(e.target.checked)}
                    className="rounded border-border text-primary focus:ring-primary"
                  />
                  <Label htmlFor="detect-duplicates" className="text-sm text-muted-foreground cursor-pointer">
                    {t('import.detectDuplicates')}
                  </Label>
                </div>}
              </div>

              {csvSplitColumns && csvHeaders.length > 0 && (
                <div className="grid grid-cols-2 gap-4 my-3">
                  <div>
                    <Label className="text-xs text-muted-foreground mb-1 block">{t('import.inflowColumn')}</Label>
                    <select
                      className="w-full border border-border rounded-md px-3 py-1.5 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                      value={csvInflowColumn}
                      onChange={(e) => { setCsvInflowColumn(e.target.value); rePreview({ inflow: e.target.value }) }}
                    >
                      <option value="">{t('import.selectColumn')}</option>
                      {csvHeaders.map(h => <option key={h} value={h}>{h}</option>)}
                    </select>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground mb-1 block">{t('import.outflowColumn')}</Label>
                    <select
                      className="w-full border border-border rounded-md px-3 py-1.5 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                      value={csvOutflowColumn}
                      onChange={(e) => { setCsvOutflowColumn(e.target.value); rePreview({ outflow: e.target.value }) }}
                    >
                      <option value="">{t('import.selectColumn')}</option>
                      {csvHeaders.map(h => <option key={h} value={h}>{h}</option>)}
                    </select>
                  </div>
                </div>
              )}

              {/* Column mapping — map CSV headers to Securo fields */}
              {csvHeaders.length > 0 && (
                <div className="mt-4 pt-4 border-t border-border">
                  <p className="text-xs font-medium text-muted-foreground">{t('import.columnMapping')}</p>
                  <p className="text-xs text-muted-foreground mt-0.5 mb-3">{t('import.columnMappingHint')}</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {CSV_MAPPING_FIELDS
                      .filter((f) => !(csvSplitColumns && f.key === 'amount'))
                      .map((f) => (
                        <div key={f.key}>
                          <Label className="text-xs text-muted-foreground mb-1 block">{t(f.label)}</Label>
                          <select
                            className="w-full border border-border rounded-md px-3 py-1.5 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                            value={csvColumnMapping[f.key] ?? ''}
                            onChange={(e) => handleMappingChange(f.key, e.target.value)}
                          >
                            <option value="">{t('import.columnAutoDetect')}</option>
                            {csvHeaders.map((h) => <option key={h} value={h}>{h}</option>)}
                          </select>
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Summary bar */}
          <ImportSummaryBar
            transactions={reviewTransactions}
            userCurrency={userCurrency}
            locale={locale}
          />

          {/* Review table */}
          <ImportReviewTable
            transactions={reviewTransactions}
            categories={categoriesList}
            groups={categoryGroupsList}
            userCurrency={userCurrency}
            locale={locale}
            dateLocale={dateLocale}
            searchQuery={searchQuery}
            filterCategoryIds={filterCategoryIds}
            filterUncategorized={filterUncategorized}
            statusFilter={statusFilter}
            currentPage={currentPage}
            onToggleExcluded={handleToggleExcluded}
            onChangeCategory={handleChangeCategory}
            onSearchChange={setSearchQuery}
            onCategoryIdsChange={setFilterCategoryIds}
            onUncategorizedChange={setFilterUncategorized}
            onStatusFilterChange={setStatusFilter}
            onPageChange={setCurrentPage}
          />

          {/* Footer actions */}
          <div className="px-4 sm:px-5 py-4 border-t border-border flex items-center justify-between">
            <button
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
              onClick={handleReset}
            >
              {t('common.cancel')}
            </button>
            <Button
              onClick={() => importMutation.mutate()}
              disabled={!selectedAccount || importMutation.isPending || includedCount === 0}
              className="gap-2"
            >
              <Upload size={14} />
              {importMutation.isPending
                ? t('common.loading')
                : t('import.importButton', { count: includedCount })}
            </Button>
          </div>
        </div>
      )}

      <ImportHistory entity="transactions" />

      {/* Unparsed CSV rows dialog */}
      <Dialog open={isFailedRowsOpen} onOpenChange={setIsFailedRowsOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col p-6">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">{t('import.failedRowsTitle')}</DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground mt-1">
              {t('import.failedRowsDesc')}
            </DialogDescription>
          </DialogHeader>
          <div className="flex-1 border border-border rounded-lg overflow-hidden bg-card text-foreground mt-4 max-h-[50vh] overflow-y-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="bg-muted text-muted-foreground border-b border-border sticky top-0">
                  <th className="px-3 py-2 font-semibold w-16">{t('import.lineNumber')}</th>
                  <th className="px-3 py-2 font-semibold">{t('import.description')}</th>
                  <th className="px-3 py-2 font-semibold">{t('import.rawValue')}</th>
                  <th className="px-3 py-2 font-semibold">{t('import.errorReason')}</th>
                </tr>
              </thead>
              <tbody>
                {previewData?.failed_rows?.map((row, idx) => (
                  <tr key={idx} className="border-b border-border last:border-0 hover:bg-muted/50">
                    <td className="px-3 py-2 text-muted-foreground">{row.line_number}</td>
                    <td className="px-3 py-2 truncate max-w-[200px]" title={row.description}>{row.description || '-'}</td>
                    <td className="px-3 py-2 font-mono break-all max-w-[150px]">{row.raw_value}</td>
                    <td className="px-3 py-2 text-amber-600 font-medium">{t(`import.errors.${row.error_reason}`)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <DialogFooter className="mt-4 pt-4 border-t border-border flex justify-end">
            <Button onClick={() => setIsFailedRowsOpen(false)} size="sm">
              {t('common.close')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/** Both importers live behind one menu entry: someone with a file to upload
    should not have to know first whether it holds transactions or orders. */
export default function ImportPage() {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') === 'investments' ? 'investments' : 'transactions'

  function selectTab(next: 'transactions' | 'investments') {
    const params = new URLSearchParams(searchParams)
    if (next === 'transactions') params.delete('tab')
    else params.set('tab', next)
    setSearchParams(params, { replace: true })
  }

  return (
    <div className="space-y-6">
      {/* The title follows the tab: "Bank statement" is about the file you
          are uploading, and an order file is not one. */}
      <PageHeader
        section={t('import.title')}
        title={tab === 'investments' ? t('assetImport.title') : t('import.subtitle')}
      />

      <div className="inline-flex items-center rounded-lg border border-border bg-muted/40 p-0.5">
        {(['transactions', 'investments'] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => selectTab(value)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === value ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t(value === 'transactions' ? 'import.tabTransactions' : 'import.tabInvestments')}
          </button>
        ))}
      </div>

      {tab === 'investments' ? <AssetImportPanel /> : <TransactionImportPanel />}
    </div>
  )
}
