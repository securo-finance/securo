import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, Download, FileUp, Info, Upload, X } from 'lucide-react'

import { assets as assetsApi, assetGroups as assetGroupsApi } from '@/lib/api'
import type { AssetImportPreview, AssetOrderImport } from '@/types'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'

/** The Securo fields a CSV column can be mapped to; `*` marks the required ones. */
const MAPPABLE_FIELDS = [
  { key: 'ticker', required: true },
  { key: 'date', required: true },
  { key: 'quantity', required: true },
  { key: 'price', required: true },
  { key: 'fee', required: false },
  { key: 'kind', required: false },
  { key: 'currency', required: false },
  { key: 'notes', required: false },
] as const

/** Matches the card the other configuration pages use (see rules.tsx). */
function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card border border-border rounded-xl p-4 sm:p-6">{children}</div>
  )
}

const SELECT_CLASS =
  'border border-border rounded-lg px-2 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

export default function AssetImportPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<AssetImportPreview | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [groupId, setGroupId] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)

  const { data: wallets } = useQuery({
    queryKey: ['asset-groups'],
    queryFn: assetGroupsApi.list,
  })

  async function runPreview(
    selected: File,
    nextMapping: Record<string, string>,
    nextGroup: string,
  ) {
    setLoading(true)
    try {
      const result = await assetsApi.previewImport(selected, {
        column_mapping: nextMapping,
        group_id: nextGroup || null,
      })
      setPreview(result)
    } catch {
      toast.error(t('assetImport.previewError'))
      setPreview(null)
    } finally {
      setLoading(false)
    }
  }

  function handleFile(selected: File | null) {
    setFile(selected)
    setPreview(null)
    setMapping({})
    if (selected) runPreview(selected, {}, groupId)
  }

  // Re-preview on every change, so the counts on screen always describe the
  // import that would actually run.
  function handleMappingChange(field: string, column: string) {
    const next = { ...mapping, [field]: column }
    if (!column) delete next[field]
    setMapping(next)
    if (file) runPreview(file, next, groupId)
  }

  function handleWalletChange(value: string) {
    setGroupId(value)
    if (file) runPreview(file, mapping, value)
  }

  async function handleImport() {
    if (!preview || preview.orders.length === 0) return
    setImporting(true)
    try {
      const result = await assetsApi.importOrders(preview.orders as AssetOrderImport[], groupId || null)
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset-groups'] })
      toast.success(t('assetImport.imported', { count: result.imported }))
      navigate('/assets')
    } catch {
      toast.error(t('assetImport.importError'))
    } finally {
      setImporting(false)
    }
  }

  const importable = preview?.orders.length ?? 0
  const rowErrors = preview?.errors ?? []
  const walletWarnings = preview?.warnings ?? []
  const needsMapping = !!preview?.parse_error

  return (
    <div>
      <PageHeader
        section={t('assets.title')}
        title={t('assetImport.title')}
        action={
          <Button variant="outline" onClick={() => assetsApi.importTemplate()}>
            <Download size={14} className="mr-1" />
            {t('assetImport.downloadTemplate')}
          </Button>
        }
      />

      <SectionCard>
        <div className="mb-4 grid gap-2 sm:max-w-xs">
          <Label htmlFor="asset-import-wallet">{t('assetImport.wallet')}</Label>
          <select
            id="asset-import-wallet"
            className={SELECT_CLASS}
            value={groupId}
            onChange={(e) => handleWalletChange(e.target.value)}
          >
            <option value="">{t('assetImport.noWallet')}</option>
            {(wallets ?? []).map((w) => (
              <option key={w.id} value={w.id}>{w.name}</option>
            ))}
          </select>
        </div>

        <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-10 text-center transition-colors hover:border-primary/50">
          <FileUp size={22} className="text-muted-foreground" />
          <span className="text-sm font-medium">
            {file ? file.name : t('assetImport.choose')}
          </span>
          <span className="text-xs text-muted-foreground">{t('assetImport.chooseHint')}</span>
          <input
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
          />
        </label>

        {loading && (
          <p className="mt-4 text-sm text-muted-foreground">{t('assetImport.reading')}</p>
        )}

        {needsMapping && (
          <div className="mt-6">
            <p className="mb-3 text-sm text-amber-500">{t('assetImport.mapPrompt')}</p>
            <div className="grid gap-3 sm:grid-cols-2">
              {MAPPABLE_FIELDS.map(({ key, required }) => (
                <div key={key} className="grid gap-1">
                  <Label htmlFor={`map-${key}`} className="text-xs">
                    {t(`assetImport.field.${key}`)}{required ? ' *' : ''}
                  </Label>
                  <select
                    id={`map-${key}`}
                    className={SELECT_CLASS}
                    value={mapping[key] ?? ''}
                    onChange={(e) => handleMappingChange(key, e.target.value)}
                  >
                    <option value="">{t('assetImport.ignoreColumn')}</option>
                    {(preview?.csv_columns ?? []).map((col) => (
                      <option key={col} value={col}>{col}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          </div>
        )}

        {preview && !needsMapping && (
          <div className="mt-6 space-y-4">
            <div className="flex flex-wrap gap-4 text-sm">
              <span>{t('assetImport.summaryOrders', { count: importable })}</span>
              <span className="text-muted-foreground">
                {t('assetImport.summaryHoldings', {
                  created: preview.holdings_created,
                  matched: preview.holdings_matched,
                })}
              </span>
              {preview.skipped > 0 && (
                <span className="text-muted-foreground">
                  {t('assetImport.summarySkipped', { count: preview.skipped })}
                </span>
              )}
            </div>

            {walletWarnings.length > 0 && (
              <div className="rounded-md border border-blue-200 bg-blue-50 p-3 dark:border-blue-800 dark:bg-blue-950">
                <p className="mb-2 flex items-center gap-2 text-sm font-medium text-blue-700 dark:text-blue-300">
                  <Info size={14} />
                  {t('assetImport.walletWarningTitle')}
                </p>
                <ul className="space-y-1 text-xs text-blue-600 dark:text-blue-300/80">
                  {walletWarnings.map((w) => (
                    <li key={`${w.ticker}-${w.reason}`}>
                      {t(`assetImport.warning.${w.reason}`, {
                        ticker: w.ticker,
                        wallet: w.wallet ?? '—',
                      })}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {rowErrors.length > 0 && (
              <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
                <p className="mb-2 flex items-center gap-2 text-sm font-medium text-amber-700 dark:text-amber-400">
                  <AlertTriangle size={14} />
                  {t('assetImport.rowsSkipped', { count: rowErrors.length })}
                </p>
                <ul className="space-y-1 text-xs text-muted-foreground">
                  {rowErrors.slice(0, 8).map((err) => (
                    <li key={`${err.row}-${err.reason}`}>
                      {t('assetImport.rowError', {
                        row: err.row,
                        ticker: err.ticker ?? '—',
                        reason: t(`assetImport.reason.${err.reason}`, err.reason),
                      })}
                      {err.detail ? ` (${err.detail})` : ''}
                    </li>
                  ))}
                  {rowErrors.length > 8 && (
                    <li>{t('assetImport.moreErrors', { count: rowErrors.length - 8 })}</li>
                  )}
                </ul>
              </div>
            )}

            {importable > 0 && (
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 text-left">{t('assetImport.field.ticker')}</th>
                      <th className="px-3 py-2 text-left">{t('assetImport.field.date')}</th>
                      <th className="px-3 py-2 text-left">{t('assetImport.field.kind')}</th>
                      <th className="px-3 py-2 text-right">{t('assetImport.field.quantity')}</th>
                      <th className="px-3 py-2 text-right">{t('assetImport.field.price')}</th>
                      <th className="px-3 py-2 text-right">{t('assetImport.field.fee')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.orders.slice(0, 50).map((order) => (
                      <tr key={order.row} className="border-t border-border/60">
                        <td className="px-3 py-1.5 font-medium">{order.ticker}</td>
                        <td className="px-3 py-1.5">{order.date}</td>
                        <td className="px-3 py-1.5">
                          <span className={order.kind === 'sell' ? 'text-rose-500' : 'text-emerald-500'}>
                            {t(`assetImport.kind.${order.kind}`)}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{order.quantity}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{order.price}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{order.fee}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {preview.orders.length > 50 && (
                  <p className="border-t border-border/60 px-3 py-2 text-xs text-muted-foreground">
                    {t('assetImport.moreRows', { count: preview.orders.length - 50 })}
                  </p>
                )}
              </div>
            )}

            <div className="flex gap-2">
              <Button onClick={handleImport} disabled={importing || importable === 0}>
                <Upload size={14} className="mr-1" />
                {importing ? t('assetImport.importing') : t('assetImport.confirm', { count: importable })}
              </Button>
              <Button variant="outline" onClick={() => handleFile(null)}>
                <X size={14} className="mr-1" />
                {t('common.cancel')}
              </Button>
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  )
}
