import { useState, useRef, useCallback } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { transactions as transactionsApi, accounts as accountsApi, importLogs as importLogsApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { Transaction, ImportLog } from '@/types'
import { Upload, FileText, X, CheckCircle2, AlertCircle, History, Trash2, Settings2, Download } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { PageHeader } from '@/components/page-header'
import { useAuth } from '@/contexts/auth-context'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

const TYPE_LABELS: Record<string, string> = {
  checking: 'accounts.typeChecking',
  savings: 'accounts.typeSavings',
  credit_card: 'accounts.typeCreditCard',
  investment: 'accounts.typeInvestment',
}

export default function ImportPage() {
  const { t, i18n } = useTranslation()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [previewData, setPreviewData] = useState<{ transactions: Transaction[]; detected_format: string } | null>(null)
  const [selectedAccount, setSelectedAccount] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [fileName, setFileName] = useState<string | null>(null)
  const [currentFile, setCurrentFile] = useState<File | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ImportLog | null>(null)
  const [csvHeaders, setCsvHeaders] = useState<string[]>([])

  // CSV options
  const [csvDateFormat, setCsvDateFormat] = useState('')
  const [csvFlipAmount, setCsvFlipAmount] = useState(false)
  const [csvSplitColumns, setCsvSplitColumns] = useState(false)
  const [csvInflowColumn, setCsvInflowColumn] = useState('')
  const [csvOutflowColumn, setCsvOutflowColumn] = useState('')

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const { data: importHistory = [] } = useQuery({
    queryKey: ['import-logs'],
    queryFn: importLogsApi.list,
  })

  const previewMutation = useMutation({
    mutationFn: ({ file, options }: { file: File; options?: { date_format?: string; flip_amount?: boolean; inflow_column?: string; outflow_column?: string } }) =>
      transactionsApi.previewImport(file, options),
    onSuccess: (data) => setPreviewData(data),
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail || t('import.processError'))
    },
  })

  const importMutation = useMutation({
    mutationFn: () => transactionsApi.import(selectedAccount, previewData!.transactions, fileName ?? '', previewData!.detected_format),
    onSuccess: (data) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['import-logs'] })
      const msg = data.skipped > 0
        ? t('import.importedWithSkipped', { imported: data.imported, skipped: data.skipped })
        : `${data.imported} ${t('import.transactionsImported')}`
      toast.success(msg)
      setPreviewData(null)
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

  const deleteMutation = useMutation({
    mutationFn: (id: string) => importLogsApi.delete(id),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['import-logs'] })
      setDeleteTarget(null)
    },
  })

  function resetCsvOptions() {
    setCsvDateFormat('')
    setCsvFlipAmount(false)
    setCsvSplitColumns(false)
    setCsvInflowColumn('')
    setCsvOutflowColumn('')
    setCsvHeaders([])
  }

  function processFile(file: File) {
    setFileName(file.name)
    setCurrentFile(file)
    resetCsvOptions()

    // Extract CSV headers for column mapping
    if (file.name.toLowerCase().endsWith('.csv')) {
      const reader = new FileReader()
      reader.onload = (e) => {
        const text = e.target?.result as string
        const firstLine = text.split('\n')[0]
        if (firstLine) {
          setCsvHeaders(firstLine.split(',').map(h => h.trim()))
        }
      }
      reader.readAsText(file)
    }

    previewMutation.mutate({ file })
  }

  const rePreview = useCallback(() => {
    if (!currentFile) return
    const options: { date_format?: string; flip_amount?: boolean; inflow_column?: string; outflow_column?: string } = {}
    if (csvDateFormat) options.date_format = csvDateFormat
    if (csvFlipAmount) options.flip_amount = true
    if (csvSplitColumns && csvInflowColumn && csvOutflowColumn) {
      options.inflow_column = csvInflowColumn
      options.outflow_column = csvOutflowColumn
    }
    previewMutation.mutate({ file: currentFile, options })
  }, [currentFile, csvDateFormat, csvFlipAmount, csvSplitColumns, csvInflowColumn, csvOutflowColumn])

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
    setFileName(null)
    setCurrentFile(null)
    setSelectedAccount('')
    resetCsvOptions()
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const isCsvFile = fileName?.toLowerCase().endsWith('.csv') ?? false

  const incomeCount = previewData?.transactions.filter(t => t.type === 'credit').length ?? 0
  const expenseCount = previewData?.transactions.filter(t => t.type === 'debit').length ?? 0

  return (
    <div className="space-y-6">
      {/* Page header */}
      <PageHeader section={t('import.title')} title={t('import.subtitle')} />

      {/* Upload zone */}
      <div
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
      </div>

      {/* Preview section */}
      {previewData && (
        <div className="bg-card rounded-xl border border-border shadow-sm">
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
                className="flex-1 border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                value={selectedAccount}
                onChange={(e) => setSelectedAccount(e.target.value)}
              >
                <option value="">{t('import.selectAccount')}</option>
                {accountsList?.map((acc) => (
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

          {/* CSV Options */}
          {isCsvFile && previewData && (
            <div className="px-5 py-4 border-b border-border bg-muted/30">
              <div className="flex items-center gap-2 mb-3">
                <Settings2 size={14} className="text-muted-foreground" />
                <p className="text-xs font-medium text-muted-foreground">{t('import.csvOptions')}</p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {/* Date format */}
                <div>
                  <Label className="text-xs text-muted-foreground mb-1 block">{t('import.dateFormat')}</Label>
                  <select
                    className="w-full border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                    value={csvDateFormat}
                    onChange={(e) => { setCsvDateFormat(e.target.value); setTimeout(rePreview, 0) }}
                  >
                    <option value="">{t('import.dateFormatAuto')}</option>
                    <option value="DD/MM/YYYY">DD/MM/YYYY</option>
                    <option value="MM/DD/YYYY">MM/DD/YYYY</option>
                    <option value="YYYY-MM-DD">YYYY-MM-DD</option>
                  </select>
                </div>

                {/* Flip amounts */}
                <div className="flex items-center gap-2 pt-4">
                  <input
                    type="checkbox"
                    id="flip-amount"
                    checked={csvFlipAmount}
                    onChange={(e) => { setCsvFlipAmount(e.target.checked); setTimeout(rePreview, 0) }}
                    className="rounded border-border text-primary focus:ring-primary"
                  />
                  <Label htmlFor="flip-amount" className="text-sm text-muted-foreground cursor-pointer">
                    {t('import.flipAmounts')}
                  </Label>
                </div>

                {/* Split columns toggle */}
                <div className="flex items-center gap-2 pt-4">
                  <input
                    type="checkbox"
                    id="split-columns"
                    checked={csvSplitColumns}
                    onChange={(e) => setCsvSplitColumns(e.target.checked)}
                    className="rounded border-border text-primary focus:ring-primary"
                  />
                  <Label htmlFor="split-columns" className="text-sm text-muted-foreground cursor-pointer">
                    {t('import.splitColumns')}
                  </Label>
                </div>
              </div>

              {/* Split column selectors */}
              {csvSplitColumns && csvHeaders.length > 0 && (
                <div className="grid grid-cols-2 gap-4 mt-3">
                  <div>
                    <Label className="text-xs text-muted-foreground mb-1 block">{t('import.inflowColumn')}</Label>
                    <select
                      className="w-full border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                      value={csvInflowColumn}
                      onChange={(e) => { setCsvInflowColumn(e.target.value); setTimeout(rePreview, 0) }}
                    >
                      <option value="">{t('import.selectColumn')}</option>
                      {csvHeaders.map(h => <option key={h} value={h}>{h}</option>)}
                    </select>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground mb-1 block">{t('import.outflowColumn')}</Label>
                    <select
                      className="w-full border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                      value={csvOutflowColumn}
                      onChange={(e) => { setCsvOutflowColumn(e.target.value); setTimeout(rePreview, 0) }}
                    >
                      <option value="">{t('import.selectColumn')}</option>
                      {csvHeaders.map(h => <option key={h} value={h}>{h}</option>)}
                    </select>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Table */}
          <div className="max-h-96 overflow-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent bg-transparent border-b border-border">
                  <TableHead className="text-xs font-medium text-muted-foreground py-3 pl-5 w-[110px]">
                    {t('transactions.date')}
                  </TableHead>
                  <TableHead className="text-xs font-medium text-muted-foreground py-3">
                    {t('transactions.description')}
                  </TableHead>
                  <TableHead className="text-xs font-medium text-muted-foreground py-3 pr-5 text-right w-[160px]">
                    {t('transactions.amount')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {previewData.transactions.slice(0, 50).map((tx, i) => (
                  <TableRow key={i} className="border-b border-border last:border-0 hover:bg-muted">
                    <TableCell className="py-3 pl-5 text-xs text-muted-foreground whitespace-nowrap">
                      {new Date(tx.date).toLocaleDateString(locale)}
                    </TableCell>
                    <TableCell className="py-3 text-sm text-foreground">{tx.description}</TableCell>
                    <TableCell className={`py-3 pr-5 text-right text-sm font-bold tabular-nums ${tx.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
                      {tx.type === 'credit' ? '+' : '−'}{formatCurrency(Math.abs(Number(tx.amount)), userCurrency, locale)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {previewData.transactions.length > 50 && (
              <p className="text-xs text-muted-foreground text-center py-3 border-t border-border">
                {t('import.showingPreview', { shown: 50, total: previewData.transactions.length })}
              </p>
            )}
          </div>

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
              disabled={!selectedAccount || importMutation.isPending}
              className="gap-2"
            >
              <Upload size={14} />
              {importMutation.isPending
                ? t('common.loading')
                : t('import.importButton', { count: previewData.transactions.length })}
            </Button>
          </div>
        </div>
      )}

      {/* Import History */}
      <div className="mt-8">
        <div className="flex items-center gap-2 mb-4">
          <History className="w-5 h-5 text-muted-foreground" />
          <h2 className="text-lg font-semibold text-foreground">{t('import.history')}</h2>
        </div>

        {importHistory.length === 0 ? (
          <div className="bg-card rounded-xl border border-border p-8 text-center text-muted-foreground">
            {t('import.noHistory')}
          </div>
        ) : (
          <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left px-3 sm:px-4 py-3 font-medium text-muted-foreground">{t('import.historyDate')}</th>
                  <th className="text-left px-3 sm:px-4 py-3 font-medium text-muted-foreground">{t('import.historyFile')}</th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground hidden lg:table-cell">{t('import.historyFormat')}</th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground hidden md:table-cell">{t('import.historyAccount')}</th>
                  <th className="text-right px-3 sm:px-4 py-3 font-medium text-muted-foreground">{t('import.historyCount')}</th>
                  <th className="text-right px-4 py-3 font-medium text-muted-foreground hidden sm:table-cell">{t('import.historyCredit')}</th>
                  <th className="text-right px-4 py-3 font-medium text-muted-foreground hidden sm:table-cell">{t('import.historyDebit')}</th>
                  <th className="px-3 sm:px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {importHistory.map((log) => (
                  <tr key={log.id} className="hover:bg-muted">
                    <td className="px-3 sm:px-4 py-3 text-xs sm:text-sm text-muted-foreground whitespace-nowrap">
                      {new Date(log.created_at).toLocaleString(locale, { dateStyle: 'short', timeStyle: 'short' })}
                    </td>
                    <td className="px-3 sm:px-4 py-3 font-mono text-xs text-foreground max-w-[120px] sm:max-w-none truncate">{log.filename || '—'}</td>
                    <td className="px-4 py-3 hidden lg:table-cell">
                      <span className="bg-muted text-muted-foreground text-xs px-2 py-0.5 rounded font-mono uppercase">
                        {log.format || '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground hidden md:table-cell">{log.account_name || '—'}</td>
                    <td className="px-3 sm:px-4 py-3 text-right text-foreground">{log.transaction_count}</td>
                    <td className="px-4 py-3 text-right text-emerald-600 font-medium hidden sm:table-cell">
                      {formatCurrency(log.total_credit, userCurrency, locale)}
                    </td>
                    <td className="px-4 py-3 text-right text-rose-600 font-medium hidden sm:table-cell">
                      {formatCurrency(log.total_debit, userCurrency, locale)}
                    </td>
                    <td className="px-3 sm:px-4 py-3 text-right">
                      <button
                        onClick={() => setDeleteTarget(log)}
                        className="text-muted-foreground hover:text-rose-500 transition-colors"
                        title={t('import.undoImport')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Delete confirmation dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('import.undoImport')}</DialogTitle>
            <DialogDescription>
              {t('import.undoDescription', { count: deleteTarget?.transaction_count, filename: deleteTarget?.filename || '—' })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              onClick={() => setDeleteTarget(null)}
              className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground"
            >
              {t('common.cancel')}
            </button>
            <button
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
              disabled={deleteMutation.isPending}
              className="px-4 py-2 text-sm bg-rose-500 text-white rounded-lg hover:bg-rose-600 disabled:opacity-50"
            >
              {deleteMutation.isPending ? t('import.deleting') : t('import.deleteAll')}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
