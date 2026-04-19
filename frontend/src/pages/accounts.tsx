import { useEffect, useState } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { accounts, connections, currencies } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import type { Account, BankConnection } from '@/types'
import {
  Building2,
  PiggyBank,
  CreditCard,
  TrendingUp,
  Wallet,
  Pencil,
  Trash2,
  RefreshCw,
  Unlink,
  Plus,
  Settings,
  Archive,
} from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { BankConnectDialog } from '@/components/bank-connect-dialog'
import { ConnectorSelectDialog } from '@/components/connector-select-dialog'
import { ConnectionSettingsDialog } from '@/components/connection-settings-dialog'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

function daysUntil(dateStr: string | null): number | null {
  if (!dateStr) return null
  const due = new Date(dateStr + 'T00:00:00')
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((due.getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

const ACCOUNT_TYPE_CONFIG: Record<string, { icon: React.ElementType; color: string; bg: string; label: string }> = {
  checking:    { icon: Building2,   color: 'text-indigo-600',    bg: 'bg-indigo-100',    label: 'accounts.typeChecking' },
  savings:     { icon: PiggyBank,   color: 'text-emerald-600', bg: 'bg-emerald-100', label: 'accounts.typeSavings' },
  credit_card: { icon: CreditCard,  color: 'text-violet-600', bg: 'bg-violet-100', label: 'accounts.typeCreditCard' },
  investment:  { icon: TrendingUp,  color: 'text-amber-600',  bg: 'bg-amber-100',  label: 'accounts.typeInvestment' },
  wallet:      { icon: Wallet,      color: 'text-rose-600',   bg: 'bg-rose-100',   label: 'accounts.typeWallet' },
}

function getTypeConfig(type: string) {
  return ACCOUNT_TYPE_CONFIG[type] ?? ACCOUNT_TYPE_CONFIG['checking']
}

export default function AccountsPage() {
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingAccount, setEditingAccount] = useState<Account | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [connectorSelectOpen, setConnectorSelectOpen] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null)
  const [settingsConnection, setSettingsConnection] = useState<BankConnection | null>(null)
  const [closingAccountId, setClosingAccountId] = useState<string | null>(null)
  const [reconnectConnId, setReconnectConnId] = useState<string | null>(null)
  const [reconnectItemId, setReconnectItemId] = useState<string | null>(null)

  const { data: accountsList, isLoading: accountsLoading } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accounts.list(),
  })

  const { data: connectionsList, isLoading: connectionsLoading } = useQuery({
    queryKey: ['connections'],
    queryFn: connections.list,
  })

  const { data: closedAccountsList } = useQuery({
    queryKey: ['accounts', 'closed'],
    queryFn: () => accounts.list(true),
  })
  const closedAccounts = closedAccountsList?.filter((a) => a.is_closed) ?? []

  const syncMutation = useMutation({
    mutationFn: (id: string) => connections.sync(id),
    onSuccess: (result) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t('accounts.syncDone'))
      const merged = (result as BankConnection & { merged_count?: number })?.merged_count
      if (merged && merged > 0) {
        toast.info(t('accounts.mergedCount', { count: merged }))
      }
    },
    onError: () => toast.error(t('accounts.syncError')),
  })

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => connections.delete(id),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t('accounts.disconnected'))
    },
  })

  const createMutation = useMutation({
    mutationFn: (data: { name: string; type: string; balance?: number; currency?: string }) =>
      accounts.create(data),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      toast.success(t('accounts.created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Account> & { id: string }) =>
      accounts.update(id, data),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      setEditingAccount(null)
      toast.success(t('accounts.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => accounts.delete(id),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setDeletingId(null)
      toast.success(t('accounts.deleted'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const closeMutation = useMutation({
    mutationFn: (id: string) => accounts.close(id),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setClosingAccountId(null)
      toast.success(t('accounts.accountClosed'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const reopenMutation = useMutation({
    mutationFn: (id: string) => accounts.reopen(id),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      toast.success(t('accounts.accountReopened'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const isLoading = accountsLoading || connectionsLoading
  const manualAccounts = accountsList?.filter((a) => a.connection_id === null) ?? []
  const bankAccounts = accountsList?.filter((a) => a.connection_id !== null) ?? []

  return (
    <div className="space-y-6">
      <PageHeader
        section={t('accounts.title')}
        title={t('accounts.title')}
        action={
          <div className="flex gap-2">
            <Button variant="outline" className="gap-1.5" onClick={() => setConnectorSelectOpen(true)}>
              <Plus size={16} />
              {t('accounts.connectBank')}
            </Button>
            <Button onClick={() => { setEditingAccount(null); setDialogOpen(true) }} className="gap-1.5">
              <Plus size={16} />
              {t('accounts.addManual')}
            </Button>
          </div>
        }
      />

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-xl" />)}
        </div>
      ) : (
        <div className="space-y-6">
          {/* Manual Accounts */}
          <div className="bg-card rounded-xl border border-border shadow-sm">
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
              <h2 className="text-sm font-medium text-muted-foreground">{t('accounts.manualAccounts')}</h2>
            </div>
            {manualAccounts.length > 0 ? (
              <div className="divide-y divide-muted">
                {manualAccounts.map((acc) => {
                  const cfg = getTypeConfig(acc.type)
                  const Icon = cfg.icon
                  const bal = Number(acc.current_balance)
                  const isCC = acc.type === 'credit_card'
                  const dueIn = isCC ? daysUntil(acc.next_due_date) : null
                  const dueText =
                    dueIn == null ? null
                      : dueIn < 0 ? t('accounts.overdue')
                      : dueIn === 0 ? t('accounts.dueToday')
                      : t('accounts.dueIn', { count: dueIn })
                  const dueClass = dueIn != null && dueIn <= 3 ? 'text-amber-600' : 'text-muted-foreground'
                  return (
                    <div key={acc.id} className="group flex items-center px-5 py-3 hover:bg-muted/50 transition-colors">
                      <Link to={`/accounts/${acc.id}`} className="flex items-center gap-3 flex-1 min-w-0">
                        <div className={`w-8 h-8 rounded-lg ${cfg.bg} flex items-center justify-center shrink-0`}>
                          <Icon size={14} className={cfg.color} />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-foreground truncate">{getAccountName(acc)}</p>
                          <p className="text-xs text-muted-foreground">
                            {t(cfg.label)}
                            {dueText && <> · <span className={dueClass}>{dueText}</span></>}
                          </p>
                        </div>
                      </Link>
                      <div className="flex items-center gap-1 mr-3 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                          onClick={() => { setEditingAccount(acc); setDialogOpen(true) }}
                          title={t('common.edit')}
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-amber-600 hover:bg-amber-50 transition-colors"
                          onClick={() => setClosingAccountId(acc.id)}
                          title={t('accounts.close')}
                        >
                          <Archive size={13} />
                        </button>
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                          onClick={() => setDeletingId(acc.id)}
                          disabled={deleteMutation.isPending}
                          title={t('common.delete')}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                      <div className="text-right">
                        <p className={`text-xs sm:text-sm font-semibold tabular-nums ${(acc.type === 'credit_card' ? bal > 0 : bal < 0) ? 'text-rose-500' : 'text-foreground'}`}>
                          {mask(formatCurrency(bal, acc.currency, locale))}
                        </p>
                        {isCC && acc.available_credit != null ? (
                          <p className="text-[10px] text-muted-foreground tabular-nums">
                            {t('accounts.availableCredit')}: {mask(formatCurrency(Number(acc.available_credit), acc.currency, locale))}
                          </p>
                        ) : acc.balance_primary != null && acc.currency !== userCurrency && (
                          <p className="text-[10px] text-muted-foreground tabular-nums">
                            {mask(formatCurrency(acc.balance_primary, userCurrency, locale))}
                          </p>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="px-5 py-8 text-center">
                <p className="text-sm text-muted-foreground">{t('accounts.noManualAccounts')}</p>
              </div>
            )}
          </div>

          {/* Bank Connections */}
          {connectionsList && connectionsList.length > 0 ? (
            <div className="space-y-3">
              {connectionsList.map((conn) => {
                const connAccounts = bankAccounts.filter((a) => a.connection_id === conn.id)
                return (
                  <div key={conn.id} className="bg-card rounded-xl border border-border shadow-sm">
                    {/* Connection header */}
                    <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center">
                          <Building2 size={14} className="text-muted-foreground" />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-semibold text-foreground">{conn.institution_name}</p>
                            <Badge
                              variant={conn.status === 'active' ? 'default' : 'secondary'}
                              className="text-[10px] px-1.5 py-0 h-4"
                            >
                              {conn.status}
                            </Badge>
                          </div>
                          {conn.last_sync_at && (
                            <p className="text-[11px] text-muted-foreground mt-0.5">
                              {t('accounts.lastSync')}: {new Date(conn.last_sync_at).toLocaleString(locale)}
                            </p>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-muted-foreground hover:text-foreground"
                          onClick={() => setSettingsConnection(conn)}
                        >
                          <Settings size={14} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-muted-foreground hover:text-foreground"
                          onClick={() => syncMutation.mutate(conn.id)}
                          disabled={syncMutation.isPending}
                        >
                          <RefreshCw size={14} className={syncMutation.isPending ? 'animate-spin' : ''} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-muted-foreground hover:text-rose-500"
                          onClick={() => disconnectMutation.mutate(conn.id)}
                          disabled={disconnectMutation.isPending}
                        >
                          <Unlink size={14} />
                        </Button>
                      </div>
                    </div>
                    {/* Reconnect banner */}
                    {conn.status !== 'active' && (
                      <div className="mx-5 mt-3 flex items-center justify-between rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5">
                        <span className="text-sm text-amber-800">
                          {t('accounts.connectionError')}
                        </span>
                        <Button
                          variant="outline"
                          size="sm"
                          className="border-amber-300 text-amber-700 hover:bg-amber-100 gap-1.5 h-8"
                          onClick={() => {
                            setReconnectConnId(conn.id)
                            setReconnectItemId(conn.external_id)
                          }}
                        >
                          <RefreshCw size={12} />
                          {t('accounts.reconnect')}
                        </Button>
                      </div>
                    )}
                    {/* Accounts list */}
                    {connAccounts.length > 0 ? (
                      <div className="divide-y divide-muted">
                        {connAccounts.map((acc) => {
                          const cfg = getTypeConfig(acc.type)
                          const Icon = cfg.icon
                          const bal = Number(acc.current_balance)
                          const isCC = acc.type === 'credit_card'
                          const dueIn = isCC ? daysUntil(acc.next_due_date) : null
                          const dueText =
                            dueIn == null ? null
                              : dueIn < 0 ? t('accounts.overdue')
                              : dueIn === 0 ? t('accounts.dueToday')
                              : t('accounts.dueIn', { count: dueIn })
                          const dueClass = dueIn != null && dueIn <= 3 ? 'text-amber-600' : 'text-muted-foreground'
                          return (
                            <div key={acc.id} className="group flex items-center px-5 py-3 hover:bg-muted/50 transition-colors">
                              <Link to={`/accounts/${acc.id}`} className="flex items-center gap-3 flex-1 min-w-0">
                                <div className={`w-8 h-8 rounded-lg ${cfg.bg} flex items-center justify-center shrink-0`}>
                                  <Icon size={14} className={cfg.color} />
                                </div>
                                <div className="min-w-0 flex-1">
                                  <p className="text-sm font-medium text-foreground truncate">{getAccountName(acc)}</p>
                                  <p className="text-xs text-muted-foreground">
                                    {t(cfg.label)}
                                    {dueText && <> · <span className={dueClass}>{dueText}</span></>}
                                  </p>
                                </div>
                              </Link>
                              <div className="flex items-center gap-1 mr-3 opacity-0 group-hover:opacity-100 transition-opacity">
                                <button
                                  className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                                  onClick={(e) => { e.preventDefault(); setEditingAccount(acc); setDialogOpen(true) }}
                                  title={t('common.edit')}
                                >
                                  <Pencil size={13} />
                                </button>
                                <button
                                  className="p-1.5 rounded-md text-muted-foreground hover:text-amber-600 hover:bg-amber-50 transition-colors"
                                  onClick={(e) => { e.preventDefault(); setClosingAccountId(acc.id) }}
                                  title={t('accounts.close')}
                                >
                                  <Archive size={13} />
                                </button>
                              </div>
                              <div className="text-right">
                                <p className={`text-xs sm:text-sm font-semibold tabular-nums ${(acc.type === 'credit_card' ? bal > 0 : bal < 0) ? 'text-rose-500' : 'text-foreground'}`}>
                                  {mask(formatCurrency(bal, acc.currency, locale))}
                                </p>
                                {isCC && acc.available_credit != null ? (
                                  <p className="text-[10px] text-muted-foreground tabular-nums">
                                    {t('accounts.availableCredit')}: {mask(formatCurrency(Number(acc.available_credit), acc.currency, locale))}
                                  </p>
                                ) : acc.balance_primary != null && acc.currency !== userCurrency && (
                                  <p className="text-[10px] text-muted-foreground tabular-nums">
                                    {mask(formatCurrency(acc.balance_primary, userCurrency, locale))}
                                  </p>
                                )}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    ) : (
                      <div className="px-5 py-4">
                        <p className="text-sm text-muted-foreground">{t('accounts.noAccountsFound')}</p>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="bg-card rounded-xl border border-dashed border-border p-8 text-center">
              <p className="text-sm text-muted-foreground">{t('accounts.noBankConnections')}</p>
            </div>
          )}

          {/* Closed Accounts */}
          {closedAccounts.length > 0 && (
            <div className="bg-card rounded-xl border border-border shadow-sm opacity-60">
              <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
                <h2 className="text-sm font-medium text-muted-foreground">{t('accounts.closedAccounts')}</h2>
              </div>
              <div className="divide-y divide-muted">
                {closedAccounts.map((acc) => {
                  const cfg = getTypeConfig(acc.type)
                  const Icon = cfg.icon
                  return (
                    <div key={acc.id} className="flex items-center px-5 py-3">
                      <div className="flex items-center gap-3 flex-1 min-w-0">
                        <div className={`w-8 h-8 rounded-lg ${cfg.bg} flex items-center justify-center shrink-0`}>
                          <Icon size={14} className={cfg.color} />
                        </div>
                        <p className="text-sm font-medium text-muted-foreground truncate">{getAccountName(acc)}</p>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-xs text-muted-foreground hover:text-foreground h-7 px-2 mr-3"
                        onClick={() => reopenMutation.mutate(acc.id)}
                        disabled={reopenMutation.isPending}
                      >
                        {t('accounts.reopen')}
                      </Button>
                      <p className="text-sm font-semibold tabular-nums text-muted-foreground w-32 text-right">
                        {mask(formatCurrency(Number(acc.current_balance), acc.currency, locale))}
                      </p>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Confirm delete dialog */}
      <Dialog open={!!deletingId} onOpenChange={() => setDeletingId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('accounts.confirmDeleteTitle')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('accounts.confirmDeleteDesc')}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeletingId(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => deletingId && deleteMutation.mutate(deletingId)}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? t('common.loading') : t('common.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Confirm close dialog */}
      <Dialog open={!!closingAccountId} onOpenChange={() => setClosingAccountId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('accounts.close')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('accounts.confirmClose')}
          </p>
          {accountsList?.find(a => a.id === closingAccountId)?.connection_id && (
            <p className="text-sm text-amber-600 font-medium">
              {t('accounts.confirmCloseBank')}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setClosingAccountId(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="default"
              onClick={() => closingAccountId && closeMutation.mutate(closingAccountId)}
              disabled={closeMutation.isPending}
            >
              {closeMutation.isPending ? t('common.loading') : t('accounts.close')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Connector Select Dialog */}
      <ConnectorSelectDialog
        open={connectorSelectOpen}
        onClose={() => setConnectorSelectOpen(false)}
        onSelect={(provider) => setSelectedProvider(provider)}
      />

      {/* Bank Connect Dialog */}
      <BankConnectDialog
        open={!!selectedProvider}
        onClose={() => setSelectedProvider(null)}
        provider={selectedProvider ?? undefined}
      />

      {/* Reconnect Dialog */}
      <BankConnectDialog
        open={!!reconnectConnId}
        onClose={() => { setReconnectConnId(null); setReconnectItemId(null) }}
        reconnectConnectionId={reconnectConnId ?? undefined}
        updateItemId={reconnectItemId ?? undefined}
      />

      {/* Connection Settings Dialog */}
      <ConnectionSettingsDialog
        open={!!settingsConnection}
        onClose={() => setSettingsConnection(null)}
        connection={settingsConnection}
      />

      {/* Account Dialog */}
      <AccountDialog
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setEditingAccount(null) }}
        account={editingAccount}
        onSave={(data) => {
          if (editingAccount) {
            updateMutation.mutate({ id: editingAccount.id, ...data })
          } else {
            createMutation.mutate(data as { name: string; type: string; balance?: number; balance_date?: string; currency?: string })
          }
        }}
        loading={createMutation.isPending || updateMutation.isPending}
      />
    </div>
  )
}

function AccountDialog({
  open,
  onClose,
  account,
  onSave,
  loading,
}: {
  open: boolean
  onClose: () => void
  account: Account | null
  onSave: (data: {
    name?: string
    display_name?: string | null
    type?: string
    balance?: number
    balance_date?: string
    currency?: string
    credit_limit?: number | null
    statement_close_day?: number | null
    payment_due_day?: number | null
  }) => void
  loading: boolean
}) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currencies.list,
    staleTime: Infinity,
  })
  const [name, setName] = useState(account?.name ?? '')
  const [displayName, setDisplayName] = useState(account?.display_name ?? '')
  const [type, setType] = useState(account?.type ?? 'checking')
  const [balance, setBalance] = useState(account?.balance?.toString() ?? '0')
  const [currency, setCurrency] = useState(account?.currency ?? userCurrency)
  const [balanceDate, setBalanceDate] = useState(new Date().toISOString().slice(0, 10))
  const [creditLimit, setCreditLimit] = useState(account?.credit_limit?.toString() ?? '')
  const [statementCloseDay, setStatementCloseDay] = useState(account?.statement_close_day?.toString() ?? '')
  const [paymentDueDay, setPaymentDueDay] = useState(account?.payment_due_day?.toString() ?? '')

  useEffect(() => {
    setName(account?.name ?? '')
    setDisplayName(account?.display_name ?? '')
    setType(account?.type ?? 'checking')
    setBalance(account?.balance?.toString() ?? '0')
    setCurrency(account?.currency ?? userCurrency)
    setBalanceDate(new Date().toISOString().slice(0, 10))
    setCreditLimit(account?.credit_limit?.toString() ?? '')
    setStatementCloseDay(account?.statement_close_day?.toString() ?? '')
    setPaymentDueDay(account?.payment_due_day?.toString() ?? '')
  }, [account])

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {account ? t('accounts.editAccount') : t('accounts.addManual')}
          </DialogTitle>
        </DialogHeader>
        <form
          key={account?.id ?? 'new'}
          onSubmit={(e) => {
            e.preventDefault()
            const isCC = type === 'credit_card'
            const parseDay = (v: string) => {
              const n = parseInt(v, 10)
              return Number.isFinite(n) && n >= 1 && n <= 31 ? n : null
            }
            const isConnected = !!account?.connection_id
            onSave({
              ...(!isConnected && { name, type, balance: parseFloat(balance), balance_date: balanceDate, currency }),
              display_name: displayName.trim() || null,
              ...(isCC && {
                credit_limit: creditLimit !== '' ? parseFloat(creditLimit) : null,
                statement_close_day: parseDay(statementCloseDay),
                payment_due_day: parseDay(paymentDueDay),
              }),
            })
          }}
          className="space-y-4"
        >
          <div className="space-y-2">
            <Label>{t('accounts.accountName')}</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} required disabled={!!account?.connection_id} />
          </div>
          {account?.connection_id && (
            <div className="space-y-2">
              <Label>{t('accounts.displayName')}</Label>
              <Input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder={name}
              />
              <p className="text-xs text-muted-foreground">{t('accounts.displayNameHint')}</p>
            </div>
          )}
          {!account?.connection_id && (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>{t('accounts.accountType')}</Label>
                  <select
                    className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                    value={type}
                    onChange={(e) => setType(e.target.value)}
                  >
                    <option value="checking">{t('accounts.typeChecking')}</option>
                    <option value="savings">{t('accounts.typeSavings')}</option>
                    <option value="credit_card">{t('accounts.typeCreditCard')}</option>
                    <option value="investment">{t('accounts.typeInvestment')}</option>
                    <option value="wallet">{t('accounts.typeWallet')}</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <Label>{t('accounts.currency')}</Label>
                  <select
                    className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                    value={currency}
                    onChange={(e) => setCurrency(e.target.value)}
                  >
                    {(supportedCurrencies ?? [{ code: userCurrency, symbol: userCurrency, name: userCurrency, flag: '' }]).map((c) => (
                      <option key={c.code} value={c.code}>{c.flag} {c.name}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>
                    {type === 'credit_card'
                      ? t('accounts.balanceCreditCard')
                      : t('accounts.balance')}
                  </Label>
                  <Input
                    type="number"
                    step="0.01"
                    min={type === 'credit_card' ? '0' : undefined}
                    value={balance}
                    onChange={(e) => setBalance(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t('accounts.balanceDate')}</Label>
                  <DatePickerInput
                    value={balanceDate}
                    onChange={setBalanceDate}
                    className="w-full justify-start"
                  />
                </div>
              </div>
              {type === 'credit_card' && (
                <p className="text-xs text-muted-foreground -mt-2">
                  {t('accounts.balanceCreditCardHint')}
                </p>
              )}
            </>
          )}
          {type === 'credit_card' && (
            <div className="space-y-4 rounded-lg border border-border bg-muted/30 p-4">
              <div className="space-y-2">
                <Label>{t('accounts.creditLimit')}</Label>
                <Input
                  type="number"
                  step="0.01"
                  min="0"
                  value={creditLimit}
                  onChange={(e) => setCreditLimit(e.target.value)}
                  placeholder="0.00"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>{t('accounts.statementCloseDay')}</Label>
                  <Input
                    type="number"
                    min="1"
                    max="31"
                    value={statementCloseDay}
                    onChange={(e) => setStatementCloseDay(e.target.value)}
                    placeholder={t('accounts.dayOfMonthHint')}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t('accounts.paymentDueDay')}</Label>
                  <Input
                    type="number"
                    min="1"
                    max="31"
                    value={paymentDueDay}
                    onChange={(e) => setPaymentDueDay(e.target.value)}
                    placeholder={t('accounts.dayOfMonthHint')}
                  />
                </div>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
