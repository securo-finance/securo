import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { connections } from '@/lib/api'
import type { BankConnection } from '@/types'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/page-header'
import { BankConnectDialog } from '@/components/bank-connect-dialog'
import { ConnectorSelectDialog } from '@/components/connector-select-dialog'
import { ConnectionSettingsDialog } from '@/components/connection-settings-dialog'
import { useCurrentMonth } from '@/hooks/use-current-month'
import {
  Building2,
  RefreshCw,
  Settings,
  Unlink,
} from 'lucide-react'

export default function AccountsPage() {
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const queryClient = useQueryClient()
  const { data: currentMonthState } = useCurrentMonth()
  const currentMonthDefined = currentMonthState?.is_defined ?? false
  const isSnapshotView = currentMonthState?.is_snapshot_view ?? false
  const editableMonth = currentMonthDefined && !isSnapshotView

  const [connectorSelectOpen, setConnectorSelectOpen] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null)
  const [settingsConnection, setSettingsConnection] = useState<BankConnection | null>(null)
  const [reconnectConnId, setReconnectConnId] = useState<string | null>(null)
  const [reconnectItemId, setReconnectItemId] = useState<string | null>(null)
  const [syncingConnectionId, setSyncingConnectionId] = useState<string | null>(null)

  const { data: connectionsList, isLoading } = useQuery({
    queryKey: ['connections'],
    queryFn: connections.list,
  })

  const syncMutation = useMutation({
    mutationFn: (id: string) => connections.sync(id),
    onMutate: (id) => setSyncingConnectionId(id),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      toast.success(t('accounts.syncDone'))
      const merged = (result as BankConnection & { merged_count?: number })?.merged_count
      if (merged && merged > 0) {
        toast.info(t('accounts.mergedCount', { count: merged }))
      }
    },
    onError: () => toast.error(t('accounts.syncError')),
    onSettled: () => setSyncingConnectionId(null),
  })

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => connections.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t('accounts.disconnected'))
    },
    onError: () => toast.error(t('common.error')),
  })

  return (
    <div className="space-y-6">
      <PageHeader
        section={t('accounts.title')}
        title={t('accounts.title')}
        action={
          <Button
            variant="outline"
            className="gap-1.5"
            onClick={() => setConnectorSelectOpen(true)}
            disabled={!editableMonth}
            title={!currentMonthDefined ? t('common.currentMonthLocked') : isSnapshotView ? t('common.snapshotReadOnlyLocked') : undefined}
          >
            <Building2 size={16} />
            {t('accounts.connectBank')}
          </Button>
        }
      />

      {!currentMonthDefined ? (
        <div className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground shadow-sm">
          <p className="font-medium text-foreground">{t('common.currentMonthLocked')}</p>
          <p className="mt-1">{t('accounts.currentMonthGuard')}</p>
        </div>
      ) : isSnapshotView ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 shadow-sm">
          <p className="font-medium">{t('common.snapshotReadOnlyTitle', { period: currentMonthState?.selected_period_label })}</p>
          <p className="mt-1">{t('common.snapshotReadOnlyHint')}</p>
        </div>
      ) : null}

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-20 rounded-xl" />
          ))}
        </div>
      ) : connectionsList && connectionsList.length > 0 ? (
        <div className="space-y-3">
          {connectionsList.map((connection) => {
            const displayName = connection.settings?.display_name || connection.institution_name
            const billImportEnabled = connection.settings?.bill_import_enabled ?? true

            return (
              <div key={connection.id} className="rounded-xl border border-border bg-card shadow-sm">
                <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
                  <div className="flex items-center gap-3">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-muted">
                      <Building2 size={14} className="text-muted-foreground" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-semibold text-foreground">{displayName}</p>
                        <Badge variant={connection.status === 'active' ? 'default' : 'secondary'} className="h-4 px-1.5 py-0 text-[10px]">
                          {connection.status}
                        </Badge>
                      </div>
                      {connection.last_sync_at ? (
                        <p className="mt-0.5 text-[11px] text-muted-foreground">
                          {t('accounts.lastSync')}: {new Date(connection.last_sync_at).toLocaleString(locale)}
                        </p>
                      ) : null}
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0 text-muted-foreground hover:text-foreground"
                      onClick={() => syncMutation.mutate(connection.id)}
                      disabled={syncMutation.isPending || !editableMonth}
                      title={syncingConnectionId === connection.id ? t('accounts.fetchingBills') : t('accounts.fetchBills')}
                    >
                      <RefreshCw size={14} className={syncingConnectionId === connection.id ? 'animate-spin' : ''} />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0 text-muted-foreground hover:text-foreground"
                      onClick={() => setSettingsConnection(connection)}
                      disabled={!editableMonth}
                      title={t('connections.settings')}
                    >
                      <Settings size={14} />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0 text-muted-foreground hover:text-rose-500"
                      onClick={() => disconnectMutation.mutate(connection.id)}
                      disabled={disconnectMutation.isPending || !editableMonth}
                      title={t('accounts.disconnect')}
                    >
                      <Unlink size={14} />
                    </Button>
                  </div>
                </div>

                <div className="flex items-center justify-between px-5 py-3">
                  <div>
                    <p className="text-sm font-medium text-foreground">{t('connections.billImportEnabled')}</p>
                    <p className="text-xs text-muted-foreground">
                      {billImportEnabled ? t('accounts.billImportEnabled') : t('accounts.billImportDisabled')}
                    </p>
                  </div>
                  <Badge variant={billImportEnabled ? 'default' : 'secondary'}>
                    {billImportEnabled ? t('accounts.billImportEnabled') : t('accounts.billImportDisabled')}
                  </Badge>
                </div>

                {connection.status !== 'active' ? (
                  <div className="mx-5 mb-5 flex items-center justify-between rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5">
                    <span className="text-sm text-amber-800">{t('accounts.connectionError')}</span>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 gap-1.5 border-amber-300 text-amber-700 hover:bg-amber-100"
                      onClick={() => {
                        setReconnectConnId(connection.id)
                        setReconnectItemId(connection.external_id)
                      }}
                      disabled={!editableMonth}
                    >
                      <RefreshCw size={12} />
                      {t('accounts.reconnect')}
                    </Button>
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-border bg-card p-8 text-center">
          <p className="text-sm text-muted-foreground">{t('accounts.noBankConnections')}</p>
        </div>
      )}

      <ConnectorSelectDialog
        open={connectorSelectOpen}
        onClose={() => setConnectorSelectOpen(false)}
        onSelect={(provider) => setSelectedProvider(provider)}
      />

      <BankConnectDialog
        open={!!selectedProvider}
        onClose={() => setSelectedProvider(null)}
        provider={selectedProvider ?? undefined}
      />

      <BankConnectDialog
        open={!!reconnectConnId}
        onClose={() => {
          setReconnectConnId(null)
          setReconnectItemId(null)
        }}
        reconnectConnectionId={reconnectConnId ?? undefined}
        updateItemId={reconnectItemId ?? undefined}
      />

      <ConnectionSettingsDialog
        open={!!settingsConnection}
        onClose={() => setSettingsConnection(null)}
        connection={settingsConnection}
      />
    </div>
  )
}
