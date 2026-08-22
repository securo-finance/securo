import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { History, Trash2 } from 'lucide-react'

import { importLogs as importLogsApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import type { ImportLog } from '@/types'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface ImportHistoryProps {
  /** Which importer's runs to list; the two never mix in one table. */
  entity: 'transactions' | 'asset_orders'
}

/**
 * Past imports, newest first, each removable.
 *
 * Deleting an entry is the undo: the server takes the rows back out, and for
 * orders it also recomputes the affected positions. The two entities share
 * this table but not its columns — an order import has no account and no
 * credit/debit totals, so those columns only appear for statements.
 */
export function ImportHistory({ entity }: ImportHistoryProps) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const [deleteTarget, setDeleteTarget] = useState<ImportLog | null>(null)

  const { data: logs = [] } = useQuery({
    queryKey: ['import-logs'],
    queryFn: importLogsApi.list,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => importLogsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['import-logs'] })
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      invalidateFinancialQueries(queryClient)
      setDeleteTarget(null)
      toast.success(t('import.undone'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const rows = logs.filter((log) => (log.entity ?? 'transactions') === entity)
  const isStatement = entity === 'transactions'

  return (
    <div className="mt-8">
      <div className="mb-4 flex items-center gap-2">
        <History className="h-5 w-5 text-muted-foreground" />
        <h2 className="text-lg font-semibold text-foreground">{t('import.history')}</h2>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground">
          {t('import.noHistory')}
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="px-3 py-3 text-left font-medium text-muted-foreground sm:px-4">{t('import.historyDate')}</th>
                <th className="px-3 py-3 text-left font-medium text-muted-foreground sm:px-4">{t('import.historyFile')}</th>
                <th className="hidden px-4 py-3 text-left font-medium text-muted-foreground lg:table-cell">{t('import.historyFormat')}</th>
                {isStatement && (
                  <th className="hidden px-4 py-3 text-left font-medium text-muted-foreground md:table-cell">{t('import.historyAccount')}</th>
                )}
                <th className="px-3 py-3 text-right font-medium text-muted-foreground sm:px-4">{t('import.historyCount')}</th>
                {isStatement && (
                  <>
                    <th className="hidden px-4 py-3 text-right font-medium text-muted-foreground sm:table-cell">{t('import.historyCredit')}</th>
                    <th className="hidden px-4 py-3 text-right font-medium text-muted-foreground sm:table-cell">{t('import.historyDebit')}</th>
                  </>
                )}
                <th className="px-3 py-3 sm:px-4" aria-label={t('common.more')}></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((log) => (
                <tr key={log.id} className="hover:bg-muted">
                  <td className="px-3 py-3 text-xs whitespace-nowrap text-muted-foreground sm:px-4 sm:text-sm">
                    {new Date(log.created_at).toLocaleString(dateLocale, { dateStyle: 'short', timeStyle: 'short' })}
                  </td>
                  <td className="max-w-[120px] truncate px-3 py-3 font-mono text-xs text-foreground sm:max-w-none sm:px-4">
                    {log.filename || '—'}
                  </td>
                  <td className="hidden px-4 py-3 lg:table-cell">
                    <span className="rounded bg-muted px-2 py-0.5 font-mono text-xs uppercase text-muted-foreground">
                      {log.format || '—'}
                    </span>
                  </td>
                  {isStatement && (
                    <td className="hidden px-4 py-3 text-muted-foreground md:table-cell">{log.account_name || '—'}</td>
                  )}
                  <td className="px-3 py-3 text-right text-foreground sm:px-4">{log.transaction_count}</td>
                  {isStatement && (
                    <>
                      <td className="hidden px-4 py-3 text-right font-medium text-emerald-600 sm:table-cell">
                        {formatCurrency(log.total_credit, userCurrency, locale)}
                      </td>
                      <td className="hidden px-4 py-3 text-right font-medium text-rose-600 sm:table-cell">
                        {formatCurrency(log.total_debit, userCurrency, locale)}
                      </td>
                    </>
                  )}
                  <td className="px-3 py-3 text-right sm:px-4">
                    {canWrite && (
                      <button
                        onClick={() => setDeleteTarget(log)}
                        className="text-muted-foreground transition-colors hover:text-rose-500"
                        aria-label={t('import.undoImport')}
                        title={t('import.undoImport')}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('import.undoImport')}</DialogTitle>
            <DialogDescription>
              {t(isStatement ? 'import.undoDescription' : 'import.undoOrdersDescription', {
                count: deleteTarget?.transaction_count,
                filename: deleteTarget?.filename || '—',
              })}
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
              className="rounded-lg bg-rose-500 px-4 py-2 text-sm text-white hover:bg-rose-600 disabled:opacity-50"
            >
              {deleteMutation.isPending ? t('import.deleting') : t('import.deleteAll')}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
