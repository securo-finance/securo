import { useState, useRef, useCallback, useEffect } from 'react'
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
import type {
  AccountType,
  ImportPreviewTransaction,
  ImportReviewTransaction,
  FailedRow,
  ImportPresetChoice,
  DryRunSummary,
  PresetCategorySuggestion,
} from '@/types'
import { Upload, FileText, X, CheckCircle2, AlertCircle, Settings2, Download } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { PageHeader } from '@/components/page-header'
import { AssetImportPanel } from '@/components/asset-import-panel'
import { ImportSummaryBar } from '@/components/import-summary-bar'
import { ImportReviewTable } from '@/components/import-review-table'
import { ImportHistory } from '@/components/import-history'
import { ImportCategoryMapping, type MappingChoice } from '@/components/import-category-mapping'
import { ImportReportDialog, type ImportReportEntry, type ImportReportAccountLine } from '@/components/import-report-dialog'
import { AccountRouting, ROUTE_SKIP, type RoutingTarget } from '@/components/import-account-routing'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'

const TYPE_LABELS: Record<AccountType, string> = {
  checking: 'accounts.typeChecking',
  savings: 'accounts.typeSavings',
  credit_card: 'accounts.typeCreditCard',
  loan: 'accounts.typeLoan',
  investment: 'accounts.typeInvestment',
  wallet: 'accounts.typeWallet',
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

/** Apply the per-import remembered mapping (§4.3) onto freshly previewed
    rows: the server's suggestion is the default, the user's card choice
    overrides it, 'rules' contributes nothing (§4.4), and `null` forces
    Uncategorategorized through the existing per-row field. Runs on every
    preview response so the mapping survives each rePreview round-trip. */
function applyMappingChoices(
  txns: ImportReviewTransaction[],
  suggestions: PresetCategorySuggestion[],
  choices: Record<string, MappingChoice>,
): ImportReviewTransaction[] {
  const targets: Record<string, MappingChoice | undefined> = {}
  for (const s of suggestions) {
    if (s.suggested_category_id) targets[s.value] = s.suggested_category_id
  }
  for (const [value, choice] of Object.entries(choices)) targets[value] = choice
  return txns.map((tx) => {
    const target = targets[tx.category_name ?? '']
    if (target === undefined || target === 'rules') return tx // no contribution
    return { ...tx, selected_category_id: target }
  })
}

type ImportResult = Awaited<ReturnType<typeof transactionsApi.import>>
type BucketStatus = 'not-started' | 'in-flight' | 'acknowledged' | 'proven-not-committed' | 'outcome-unknown'
interface ImportBucket {
  id: number
  accountId: string
  name: string
  rows: readonly ImportPreviewTransaction[]
  sourceBuckets: readonly { id: string; name: string; submitted: number; excluded: number }[]
  membership: readonly string[]
  options?: { detect_duplicates: boolean }
  status: BucketStatus
  result?: ImportResult
}
interface ImportPlan {
  id: number
  routingSkipped: number
  filename: string
  format: string
  options?: { detect_duplicates: boolean }
  buckets: ImportBucket[]
}

// A response is an acknowledgement only if it accounts for the entire bucket
// and identifies its committed log. Malformed success is not safe to retry.
function isAcknowledgement(data: ImportResult, rowCount: number): boolean {
  return !!data && [data.imported, data.skipped, data.excluded].every((n) => Number.isSafeInteger(n) && n >= 0)
    && data.imported + data.skipped + data.excluded === rowCount
    && typeof data.import_log_id === 'string'
    && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(data.import_log_id)
}

function TransactionImportPanel({ abandonmentGuard }: { abandonmentGuard: React.RefObject<(() => boolean) | null> }) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [previewData, setPreviewData] = useState<{ transactions: ImportPreviewTransaction[]; detected_format: string; csv_columns?: string[]; parse_error?: string | null; failed_rows?: FailedRow[]; preset?: string | null; dry_run?: DryRunSummary | null; preset_category_suggestions?: PresetCategorySuggestion[] } | null>(null)
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

  // Undefined inherits the selected preset; false/true and date choices
  // (including auto) remain explicit across every preview and preset change.
  const [csvDateFormat, setCsvDateFormat] = useState<string | undefined>()
  const [csvFlipAmount, setCsvFlipAmount] = useState<boolean | undefined>()
  const previewRevision = useRef(0)
  const [successfulPreviewRevision, setSuccessfulPreviewRevision] = useState<number | null>(null)
  const rowEdits = useRef(false)
  const resetOnReplacement = useRef(false)
  const previewPending = useRef(false)
  const [reviewRequired, setReviewRequired] = useState(false)
  const [reviewedRevision, setReviewedRevision] = useState<number | null>(null)
  const nextPlanId = useRef(0)
  const mappingChoicesRef = useRef<Record<string, MappingChoice>>({})
  const [csvDetectDuplicates, setCsvDetectDuplicates] = useState(true)
  const [csvSplitColumns, setCsvSplitColumns] = useState(false)
  const [csvInflowColumn, setCsvInflowColumn] = useState('')
  const [csvOutflowColumn, setCsvOutflowColumn] = useState('')
  const [csvColumnMapping, setCsvColumnMapping] = useState<Record<string, string>>({})
  // Source selector: auto stays generic; Monarch is an explicit confirmation.
  const [csvPreset, setCsvPreset] = useState<ImportPresetChoice>('auto')
  // Per-import remembered mapping (§4.3): value -> category id | null
  // (force uncategorized) | 'rules' (contribute nothing). Separate state so
  // it survives every rePreview round-trip; re-applied to rows on response.
  const [mappingChoices, setMappingChoices] = useState<Record<string, MappingChoice>>({})
  // Combined-export routing (§7.2): Monarch account value -> target account
  // id, or ROUTE_SKIP. Normalized-name prefill is derived, not stored.
  const [routing, setRouting] = useState<Record<string, string>>({})
  const planRef = useRef<ImportPlan | null>(null)
  const dispatching = useRef(false)
  const [plan, setPlan] = useState<ImportPlan | null>(null)
  const planLocked = plan !== null
  const publishPlan = () => setPlan(planRef.current ? { ...planRef.current, buckets: planRef.current.buckets.map((b) => ({ ...b })) } : null)
  const refreshImportQueries = () => {
    invalidateFinancialQueries(queryClient)
    for (const key of ['import-logs', 'payees', 'categories', 'category-groups']) {
      queryClient.invalidateQueries({ queryKey: [key] })
    }
  }

  useEffect(() => {
    const mayLeave = () => !planRef.current || (!dispatching.current && window.confirm(t('import.abandonProgress')))
    abandonmentGuard.current = mayLeave
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (planRef.current) { event.preventDefault(); event.returnValue = '' }
    }
    const beforeLink = (event: MouseEvent) => {
      const link = (event.target as Element)?.closest?.('a[href]')
      if (link && !mayLeave()) { event.preventDefault(); event.stopPropagation() }
    }
    window.addEventListener('beforeunload', beforeUnload)
    document.addEventListener('click', beforeLink, true)
    return () => {
      abandonmentGuard.current = null
      window.removeEventListener('beforeunload', beforeUnload)
      document.removeEventListener('click', beforeLink, true)
    }
  }, [abandonmentGuard, t])
  const [reportOpen, setReportOpen] = useState(false)
  const [reportData, setReportData] = useState<{
    entries: ImportReportEntry[]
    imported: number
    skipped: number
    failedRows: number
    accountLines?: ImportReportAccountLine[]
    routingSkipped?: number
  } | null>(null)

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
    mutationFn: ({ file, options }: { file: File; revision: number; options?: { date_format?: string; flip_amount?: boolean; inflow_column?: string; outflow_column?: string; column_mapping?: Record<string, string>; preset?: ImportPresetChoice; dry_run?: boolean; account_id?: string } }) =>
      transactionsApi.previewImport(file, options),
    onSuccess: (data, { revision }) => {
      if (revision !== previewRevision.current) return
      previewPending.current = false
      rowEdits.current = false
      if (resetOnReplacement.current) setReviewRequired(true)
      setSuccessfulPreviewRevision(data.parse_error ? null : revision)
      setPreviewData(data)
      setCsvHeaders(data.csv_columns ?? [])
      // The remembered mapping re-applies to each fresh preview (§4.3).
      setReviewTransactions(
        data.preset === 'monarch' ? applyMappingChoices(
          toReviewTransactions(data.transactions),
          data.preset_category_suggestions ?? [],
          mappingChoicesRef.current,
        ) : toReviewTransactions(data.transactions),
      )
      setSearchQuery('')
      setFilterCategoryIds([])
      setFilterUncategorized(false)
      setStatusFilter('all')
      setCurrentPage(1)
    },
    onError: (error: unknown, { revision }) => {
      if (revision !== previewRevision.current) return
      previewPending.current = false
      setSuccessfulPreviewRevision(null)
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail || t('import.processError'))
    },
  })

  const importMutation = useMutation({
    mutationFn: async () => {
      const toPayload = (rt: ImportReviewTransaction) => ({
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
          : undefined,
        force_uncategorized: rt.selected_category_id === null,
        account_hint: rt.account_hint ?? undefined,
      })
      if (!planRef.current) {
        if (!hasCurrentPreview || successfulPreviewRevision !== previewRevision.current || !reviewConfirmed || previewPending.current) throw new Error('A reviewed current preview is required')
        const groups = new Map<string, ImportReviewTransaction[]>()
        let routingSkipped = 0
        for (const row of reviewTransactions) {
          const target = row.account_hint ? effectiveRouting[row.account_hint] : selectedAccount
          if (target === ROUTE_SKIP) { routingSkipped += 1; continue }
          if (!target) throw new Error('Every row requires a destination')
          const rows = groups.get(target) ?? []
          rows.push(row)
          groups.set(target, rows)
        }
        const options = isCsvFile ? Object.freeze({ detect_duplicates: csvDetectDuplicates }) : undefined
        const requests = [...groups].map(([accountId, rows], id) => {
          const sources = new Map<string, { id: string; name: string; submitted: number; excluded: number }>()
          const membership = rows.map((row) => {
            const sourceId = JSON.stringify(row.account_hint ? ['hint', row.account_hint] : ['unassigned'])
            const source = sources.get(sourceId) ?? { id: sourceId, name: row.account_hint || t('import.unassignedSource'), submitted: 0, excluded: 0 }
            if (row.excluded) source.excluded += 1
            else source.submitted += 1
            sources.set(sourceId, source)
            return sourceId
          })
          const payload = Object.freeze(rows.map((row) => Object.freeze(toPayload(row))))
          // Both shipped nginx configurations cap bodies at 15 MiB;
          // the parser/schema impose no row-count cap. Never split to evade it.
          const bytes = new Blob([JSON.stringify({ account_id: accountId, transactions: payload, filename: fileName ?? '', detected_format: previewData!.detected_format, ...options })]).size
          if (bytes > 15 * 1024 * 1024) throw new Error(t('import.groupTooLarge'))
          const account = (accountsList ?? []).find((a) => a.id === accountId)
          return { id, accountId, name: account ? getAccountName(account) : accountId, rows: payload, sourceBuckets: Object.freeze([...sources.values()].map((source) => Object.freeze(source))), membership: Object.freeze(membership), options, status: 'not-started' as BucketStatus }
        })
        planRef.current = {
          id: ++nextPlanId.current,
          routingSkipped,
          filename: fileName ?? '',
          format: previewData!.detected_format,
          options,
          buckets: requests,
        }
        previewRevision.current += 1 // no outstanding preview may replace the plan
        publishPlan()
      }
      const retained = planRef.current
      if (retained.buckets.some((b) => b.status === 'outcome-unknown' || b.status === 'in-flight')) {
        throw new Error('Import outcome requires reconciliation')
      }
      for (const bucket of retained.buckets) {
        if (bucket.status === 'acknowledged') continue
        bucket.status = 'in-flight'
        publishPlan()
        try {
          const result = await transactionsApi.import(
            bucket.accountId, bucket.rows, retained.filename, retained.format, bucket.options,
          )
          if (!isAcknowledgement(result, bucket.rows.length)) throw new Error('Malformed import acknowledgement')
          bucket.result = Object.freeze({ ...result })
          bucket.status = 'acknowledged'
          publishPlan()
          refreshImportQueries()
        } catch (error) {
          const response = (error as { response?: { status?: number; headers?: Record<string, string>; data?: { detail?: string } } })?.response
          // Only this endpoint's explicitly marked pre-write rejection proves
          // non-commit. Arbitrary HTTP errors, cancellation and lost responses
          // say nothing about whether the service's commit succeeded.
          const rejected = response?.status === 404
            && response.headers?.['x-import-outcome'] === 'not-committed'
            && response.data?.detail === 'Account not found'
          bucket.status = rejected ? 'proven-not-committed' : 'outcome-unknown'
          publishPlan()
          if (!rejected) refreshImportQueries()
          throw error
        }
      }
      return {
        imported: retained.buckets.reduce((sum, b) => sum + b.result!.imported, 0),
        skipped: retained.buckets.reduce((sum, b) => sum + b.result!.skipped, 0),
        excluded: retained.buckets.reduce((sum, b) => sum + b.result!.excluded, 0),
        accountLines: retained.buckets.map((b) => ({ name: b.name, imported: b.result!.imported, skipped: b.result!.skipped })),
        routingSkipped: retained.routingSkipped,
      }
    },
    onSuccess: (data) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['import-logs'] })
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      queryClient.invalidateQueries({ queryKey: ['categories'] })
      // Report frozen submitted category intentions, not card defaults or
      // persisted rule outcomes. One source value may have several intentions.
      if (previewData?.preset === 'monarch') {
        const intentions = new Map<string, ImportReportEntry>()
        for (const bucket of planRef.current!.buckets) {
          for (const row of bucket.rows) {
            if (row.excluded || row.category_name == null) continue
            const targetId = row.force_uncategorized ? null : (row.category_id ?? null)
            const status = row.force_uncategorized ? 'uncategorized' : targetId ? 'mapped' : 'rules'
            const key = JSON.stringify([row.category_name, status, targetId])
            const entry = intentions.get(key)
            if (entry) { entry.rows += 1; continue }
            intentions.set(key, {
              value: row.category_name,
              rows: 1,
              status,
              targetId,
              targetName: targetId ? categoriesList.find((c) => c.id === targetId)?.name
                ?? previewData.preset_category_suggestions?.find((s) => s.suggested_category_id === targetId)?.suggested_category_name
                ?? targetId : null,
            })
          }
        }
        const entries = [...intentions.values()]
        setReportData({
          entries,
          imported: data.imported,
          skipped: data.skipped ?? 0,
          failedRows: (previewData.failed_rows ?? []).length,
          accountLines: data.accountLines,
          routingSkipped: data.routingSkipped,
        })
        setReportOpen(true)
      }
      const hasSkippedOrExcluded = (data.skipped ?? 0) > 0 || (data.excluded ?? 0) > 0
      const msg = hasSkippedOrExcluded
        ? t('import.importedWithExcluded', { imported: data.imported, skipped: data.skipped ?? 0, excluded: data.excluded ?? 0 })
        : `${data.imported} ${t('import.transactionsImported')}`
      toast.success(msg)
      planRef.current = null
      setPlan(null)
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
      toast.error(detail || (error instanceof Error ? error.message : t('import.importError')))
    },
    onSettled: () => { dispatching.current = false },
  })

  function resetCsvOptions() {
    setCsvDateFormat(undefined)
    setCsvFlipAmount(undefined)
    mappingChoicesRef.current = {}
    previewRevision.current += 1
    setSuccessfulPreviewRevision(null)
    rowEdits.current = false
    resetOnReplacement.current = false
    setReviewRequired(false)
    setReviewedRevision(null)
    setCsvDetectDuplicates(true)
    setCsvSplitColumns(false)
    setCsvInflowColumn('')
    setCsvOutflowColumn('')
    setCsvColumnMapping({})
    setCsvHeaders([])
    // File-level resets: a new file starts generic with a fresh
    // per-import mapping session (§4.3 — memory lasts one import).
    setCsvPreset('auto')
    setMappingChoices({})
    setRouting({})
  }

  function abandonPlan() {
    if (dispatching.current) return false
    if (planRef.current && !window.confirm(t('import.abandonProgress'))) return false
    if (planRef.current?.buckets.some((b) => b.status === 'outcome-unknown')) {
      toast.error(t('import.outcomeUnknown'))
      return false
    }
    planRef.current = null
    setPlan(null)
    return true
  }

  function processFile(file: File) {
    if (!abandonPlan()) return
    setPreviewData(null)
    setReviewTransactions([])
    setFileName(file.name)
    setCurrentFile(file)
    resetCsvOptions()
    // CSV headers come back from the preview response (csv_columns), which
    // parses the file server-side and handles any delimiter/quoting.
    previewPending.current = true
    previewMutation.mutate({ file, revision: ++previewRevision.current })
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
    preset?: ImportPresetChoice
    account?: string
    choice?: { value: string; choice: MappingChoice }
  }) => {
    if (!currentFile || planRef.current) return
    if (rowEdits.current && !window.confirm(t('import.resetReviewConfirm'))) return
    if (rowEdits.current) resetOnReplacement.current = true
    setReviewedRevision(null)
    if (overrides?.date_format !== undefined) setCsvDateFormat(overrides.date_format)
    if (overrides?.flip_amount !== undefined) setCsvFlipAmount(overrides.flip_amount)
    if (overrides?.split !== undefined) setCsvSplitColumns(overrides.split)
    if (overrides?.inflow !== undefined) setCsvInflowColumn(overrides.inflow)
    if (overrides?.outflow !== undefined) setCsvOutflowColumn(overrides.outflow)
    if (overrides?.mapping !== undefined) setCsvColumnMapping(overrides.mapping)
    if (overrides?.preset !== undefined) setCsvPreset(overrides.preset)
    if (overrides?.account !== undefined) setSelectedAccount(overrides.account)
    if (overrides?.choice) {
      mappingChoicesRef.current = { ...mappingChoicesRef.current, [overrides.choice.value]: overrides.choice.choice }
      setMappingChoices(mappingChoicesRef.current)
    }
    const dateFormat = overrides?.date_format ?? csvDateFormat
    const flip = overrides?.flip_amount ?? csvFlipAmount
    const split = overrides?.split ?? csvSplitColumns
    const inflow = overrides?.inflow ?? csvInflowColumn
    const outflow = overrides?.outflow ?? csvOutflowColumn
    const mapping = overrides?.mapping ?? csvColumnMapping
    const preset = overrides?.preset ?? csvPreset
    const accountId = overrides?.account ?? selectedAccount

    const options: { date_format?: string; flip_amount?: boolean; inflow_column?: string; outflow_column?: string; column_mapping?: Record<string, string>; preset?: ImportPresetChoice; dry_run?: boolean; account_id?: string } = {}
    if (dateFormat !== undefined) options.date_format = dateFormat || 'auto'
    if (flip !== undefined) options.flip_amount = flip
    if (preset === 'monarch' || preset === 'generic') options.preset = preset
    if (split && inflow && outflow) {
      options.inflow_column = inflow
      options.outflow_column = outflow
    }
    const cleanMapping = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v))
    if (Object.keys(cleanMapping).length > 0) options.column_mapping = cleanMapping
    // Dry-run counts need the target account (§7.3); without one the
    // preview keeps its original shape.
    if (accountId) {
      options.dry_run = true
      options.account_id = accountId
    }
    previewPending.current = true
    previewMutation.mutate({ file: currentFile, options, revision: ++previewRevision.current })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFile, csvDateFormat, csvFlipAmount, csvSplitColumns, csvInflowColumn, csvOutflowColumn, csvColumnMapping, csvPreset, selectedAccount, previewData])

  const handleMappingChange = useCallback((field: string, column: string) => {
    rePreview({ mapping: { ...csvColumnMapping, [field]: column } })
  }, [rePreview, csvColumnMapping])

  // Source selection changes inherited defaults, never explicit choices.
  const handlePresetChange = useCallback((choice: ImportPresetChoice) => {
    rePreview({ preset: choice })
  }, [rePreview])

  // Mapping-card pick (§4.3): remembered for the whole import session and
  // re-applied on each preview response; one re-preview per change keeps
  // the dry-run counts and review rows in sync (§7.5).
  const handleMappingChoose = useCallback((value: string, choice: MappingChoice) => {
    rePreview({ choice: { value, choice } })
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
    if (!abandonPlan()) return
    setPreviewData(null)
    setReviewTransactions([])
    setFileName(null)
    setCurrentFile(null)
    setSelectedAccount('')
    resetCsvOptions()
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleToggleExcluded = useCallback((id: string) => {
    if (previewPending.current || planRef.current) return
    rowEdits.current = true
    setReviewTransactions(prev => prev.map(t =>
      t._id === id ? { ...t, excluded: !t.excluded } : t
    ))
  }, [])

  const handleToggleAllFiltered = useCallback((ids: string[], excluded: boolean) => {
    if (previewPending.current || planRef.current) return
    rowEdits.current = true
    const idSet = new Set(ids)
    setReviewTransactions(prev => prev.map(t =>
      idSet.has(t._id) ? { ...t, excluded } : t
    ))
  }, [])

  const handleChangeCategory = useCallback((id: string, categoryId: string | null) => {
    if (previewPending.current || planRef.current) return
    rowEdits.current = true
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
  // A failed or outstanding revision must never authorize the retained rows.
  const hasCurrentPreview = previewData !== null && !previewData.parse_error
    && successfulPreviewRevision === previewRevision.current
  const reviewConfirmed = !reviewRequired || reviewedRevision === previewRevision.current

  // Combined-export routing (§7.2): distinct account values + counts.
  const routingTargets: RoutingTarget[] = (() => {
    const counts = new Map<string, number>()
    for (const r of reviewTransactions) {
      if (r.account_hint) counts.set(r.account_hint, (counts.get(r.account_hint) ?? 0) + 1)
    }
    return [...counts].map(([value, count]) => ({ value, count }))
  })()
  const normKey = (s: string) => s.normalize('NFC').toLowerCase().replace(/[^\p{L}\p{N}\p{M}]/gu, '')
  const prefillFor = (hint: string) => {
    const key = normKey(hint)
    if (!key) return ''
    const matches = (accountsList ?? []).filter((a) => normKey(getAccountName(a)) === key)
    return matches.length === 1 ? matches[0].id : ''
  }
  const effectiveRouting: Record<string, string> = Object.fromEntries(
    routingTargets.map((h) => [h.value, routing[h.value] ?? prefillFor(h.value)]),
  )
  const unroutedTargets = routingTargets.filter((h) => !effectiveRouting[h.value])
  const hasUnassignedRows = reviewTransactions.some((r) => !r.account_hint)

  // Monarch semantics require the current explicit source selection.
  const suggestions = previewData?.preset_category_suggestions ?? []
  const effectivePreset = csvPreset === 'monarch' ? 'monarch' : null
  // Unmapped (§4.4): distinct values with no concrete target — no
  // suggestion, and no user-assigned category or explicit uncategorized.
  const unmappedCount = suggestions.filter((s) => {
    if (s.suggested_category_id) return false
    const choice = mappingChoices[s.value]
    return choice === undefined || choice === 'rules'
  }).length

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

      <p className="text-xs text-muted-foreground">{t('import.sessionOnly')}</p>
      {plan && (
        <div role="status" data-testid="import-progress" data-plan-id={plan.id} className="rounded-lg border border-amber-300 p-4 space-y-2">
          <p>{t('import.progressTotals', {
            imported: plan.buckets.reduce((sum, b) => sum + (b.result?.imported ?? 0), 0),
            skipped: plan.buckets.reduce((sum, b) => sum + (b.result?.skipped ?? 0), 0),
            excluded: plan.buckets.reduce((sum, b) => sum + (b.result?.excluded ?? 0), 0),
          })}</p>
          <p>{t('import.routingSkipped', { count: plan.routingSkipped })}</p>
          {plan.buckets.map((bucket) => (
            <div key={bucket.id} data-request-id={bucket.id}>
            <p>
              {bucket.id + 1}. {bucket.name}: {t(`import.bucketStatus.${bucket.status}`)}
              {bucket.result && ` (${bucket.result.imported} / ${bucket.result.skipped} / ${bucket.result.excluded}; ${bucket.result.import_log_id})`}
            </p>
            {bucket.sourceBuckets.map((source) => <p key={source.id} data-source-bucket={source.id}>{t('import.sourceCounts', { name: source.name, submitted: source.submitted, excluded: source.excluded })}</p>)}
            </div>
          ))}
          {plan.buckets.some((b) => b.status === 'outcome-unknown') && <p>{t('import.outcomeUnknown')}</p>}
        </div>
      )}
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

          <fieldset disabled={planLocked} className="min-w-0 border-0 m-0 p-0">
          {/* Account picker */}
          <div className="px-4 sm:px-5 py-4 border-b border-border bg-muted/50">
            <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4">
              <Label
                htmlFor="import-account"
                className="text-sm text-muted-foreground whitespace-nowrap shrink-0"
              >
                {t('import.importTo')}
              </Label>
              <select
                id="import-account"
                className="flex-1 border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                value={selectedAccount}
                onChange={(e) => {
                  // Dry-run counts follow the target account (§7.3).
                  rePreview({ account: e.target.value })
                }}
              >
                <option value="">{t('import.selectAccount')}</option>
                {sortAccountsByDisplayName(accountsList ?? []).map((acc) => (
                  <option key={acc.id} value={acc.id}>{getAccountName(acc)} ({t(TYPE_LABELS[acc.type as AccountType] || acc.type)})</option>
                ))}
              </select>
              {!selectedAccount && hasUnassignedRows && (
                <div className="flex items-center gap-1.5 text-xs text-amber-600 bg-amber-50 border border-amber-100 px-2.5 py-1.5 rounded-lg shrink-0">
                  <AlertCircle size={12} />
                  {t('import.selectAccountWarning')}
                </div>
              )}
              {unroutedTargets.length > 0 && (
                <div
                  className="flex items-center gap-1.5 text-xs text-amber-600 bg-amber-50 border border-amber-100 px-2.5 py-1.5 rounded-lg shrink-0"
                  data-testid="routing-blocked"
                >
                  <AlertCircle size={12} />
                  {t('import.routingBlocked')}
                </div>
              )}
            </div>
          </div>

          {/* Combined-export account routing strip (§7.2). */}
          {routingTargets.length > 0 && (
            <div className="px-4 sm:px-5 py-4 border-b border-border bg-muted/50">
              <AccountRouting
                targets={routingTargets}
                accounts={sortAccountsByDisplayName(accountsList ?? [])}
                routing={effectiveRouting}
                onRoute={(hint, target) => setRouting((prev) => ({ ...prev, [hint]: target }))}
              />
            </div>
          )}

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
                {isCsvFile && (
                  <div>
                    <Label htmlFor="source-preset" className="text-xs text-muted-foreground mb-1 block">{t('import.sourcePreset')}</Label>
                    <select
                      id="source-preset"
                      className="w-full border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                      value={csvPreset}
                      onChange={(e) => handlePresetChange(e.target.value as ImportPresetChoice)}
                    >
                      <option value="auto">{t('import.presetAuto')}</option>
                      <option value="monarch">{t('import.presetMonarch')}</option>
                      <option value="generic">{t('import.presetGeneric')}</option>
                    </select>
                  </div>
                )}
                <div>
                  <Label className="text-xs text-muted-foreground mb-1 block">{t('import.dateFormat')}</Label>
                  <select
                    className="w-full border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                    value={csvDateFormat ?? (effectivePreset === 'monarch' ? 'MM/DD/YYYY' : '')}
                    onChange={(e) => rePreview({ date_format: e.target.value })}
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
                    checked={csvFlipAmount ?? (effectivePreset === 'monarch')}
                    onChange={(e) => rePreview({ flip_amount: e.target.checked })}
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
                    onChange={(e) => rePreview({ split: e.target.checked })}
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
                      onChange={(e) => rePreview({ inflow: e.target.value })}
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
                      onChange={(e) => rePreview({ outflow: e.target.value })}
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

          {/* Category mapping card (§4.3) — only under a resolved Monarch
              preset, between the CSV options and the review table. */}
          {effectivePreset === 'monarch' && suggestions.length > 0 && (
            <ImportCategoryMapping
              suggestions={suggestions}
              choices={mappingChoices}
              categories={categoriesList}
              groups={categoryGroupsList}
              onChoose={handleMappingChoose}
            />
          )}

          {reviewRequired && <div className="px-5 py-3" role="status">
            <p>{t('import.reviewReset')}</p>
            <label><input type="checkbox" checked={reviewedRevision === previewRevision.current} disabled={!hasCurrentPreview || previewMutation.isPending} onChange={(e) => {
              // These values belong to this rendered successful preview, not
              // whichever revision a queued callback happens to run during.
              if (previewPending.current || !hasCurrentPreview || successfulPreviewRevision === null || successfulPreviewRevision !== previewRevision.current) return
              setReviewedRevision(e.target.checked ? successfulPreviewRevision : null)
            }} /> {t('import.reviewedPreview')}</label>
          </div>}
          {/* Summary bar */}
          <ImportSummaryBar
            transactions={reviewTransactions}
            userCurrency={userCurrency}
            locale={locale}
            dryRun={previewData.dry_run}
            unmappedCount={unmappedCount}
          />

          {/* Review table */}
          <fieldset disabled={previewMutation.isPending} className="min-w-0 border-0 m-0 p-0">
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
            onToggleAllFiltered={handleToggleAllFiltered}
            onChangeCategory={handleChangeCategory}
            onSearchChange={setSearchQuery}
            onCategoryIdsChange={setFilterCategoryIds}
            onUncategorizedChange={setFilterUncategorized}
            onStatusFilterChange={setStatusFilter}
            onPageChange={setCurrentPage}
          />
          </fieldset>

          </fieldset>
          {/* Footer actions */}
          <div className="px-4 sm:px-5 py-4 border-t border-border flex items-center justify-between">
            <button
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
              onClick={handleReset}
              disabled={importMutation.isPending}
            >
              {t('common.cancel')}
            </button>
            <Button
              onClick={() => {
                if (!planRef.current && (!hasCurrentPreview || successfulPreviewRevision !== previewRevision.current || !reviewConfirmed || previewPending.current)) return
                if (dispatching.current || planRef.current?.buckets.some((b) => b.status === 'outcome-unknown' || b.status === 'in-flight')) return
                dispatching.current = true
                importMutation.mutate()
              }}
              disabled={
                (!plan && (!hasCurrentPreview || !reviewConfirmed)) ||
                (!selectedAccount && hasUnassignedRows) ||
                unroutedTargets.length > 0 ||
                importMutation.isPending || previewMutation.isPending ||
                plan?.buckets.some((b) => b.status === 'outcome-unknown' || b.status === 'in-flight') ||
                includedCount === 0
              }
              className="gap-2"
            >
              <Upload size={14} />
              {importMutation.isPending
                ? t('common.loading')
                : plan ? t('import.resumeImport') : t('import.importButton', { count: includedCount })}
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

      {/* End-of-import report (§4.4) */}
      {reportData && (
        <ImportReportDialog
          open={reportOpen}
          onOpenChange={setReportOpen}
          entries={reportData.entries}
          imported={reportData.imported}
          skipped={reportData.skipped}
          failedRows={reportData.failedRows}
          accountLines={reportData.accountLines}
          routingSkipped={reportData.routingSkipped}
        />
      )}
    </div>
  )
}

/** Both importers live behind one menu entry: someone with a file to upload
    should not have to know first whether it holds transactions or orders. */
export default function ImportPage() {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const abandonmentGuard = useRef<(() => boolean) | null>(null)
  const tab = searchParams.get('tab') === 'investments' ? 'investments' : 'transactions'

  function selectTab(next: 'transactions' | 'investments') {
    if (next !== tab && abandonmentGuard.current && !abandonmentGuard.current()) return
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

      {tab === 'investments' ? <AssetImportPanel /> : <TransactionImportPanel abandonmentGuard={abandonmentGuard} />}
    </div>
  )
}
