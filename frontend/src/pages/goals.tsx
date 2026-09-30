import { createElement, useState } from 'react'
import { differenceInCalendarDays, parseISO } from 'date-fns'
import { getAccountName, sortAccountsByDisplayName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useEffectiveTimezone } from '@/hooks/use-timezone'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { goals as goalsApi, accounts as accountsApi, assets as assetsApi, assetGroups as assetGroupsApi, currencies as currenciesApi } from '@/lib/api'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from '@/components/ui/popover'
import type { Account, Asset, AssetGroup, Goal } from '@/types'
import {
  Pencil, Trash2, Plus, Pause, Play, CheckCircle2, Archive, ArchiveRestore, Target,
  ChevronDown, WalletCards, AlertTriangle,
} from 'lucide-react'
import { ICON_MAP } from '@/lib/category-icons'
import { IconPicker } from '@/components/icon-picker'
import { PageHeader } from '@/components/page-header'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { formatCurrency } from '@/lib/format'
import { extractApiError } from '@/lib/api-errors'
import { todayInTimezone } from '@/lib/date-utils'

function getGoalIcon(iconKey: string | null) {
  return (iconKey && ICON_MAP[iconKey]) || Target
}

const PRESET_COLORS = [
  '#3B82F6', '#10B981', '#F59E0B', '#EF4444',
  '#8B5CF6', '#EC4899', '#06B6D4', '#F97316',
]

const SELECT_CLASS = 'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

function LinkedResourceSelect<T extends { id: string }>({
  name,
  label,
  placeholder,
  defaultValue,
  items,
  renderOption,
  hint,
}: {
  name: string
  label: string
  placeholder: string
  defaultValue: string | null | undefined
  items: T[] | undefined
  renderOption: (item: T) => React.ReactNode
  hint?: string
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <select name={name} defaultValue={defaultValue ?? ''} className={SELECT_CLASS} required>
        <option value="">{placeholder}</option>
        {items?.map((item) => (
          <option key={item.id} value={item.id}>
            {renderOption(item)}
          </option>
        ))}
      </select>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}
function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-semibold text-foreground">{title}</p>
      {action}
    </div>
  )
}

function OnTrackBadge({ status, t }: { status: string | null; t: (key: string) => string }) {
  if (!status) return null
  const config: Record<string, { bg: string; text: string; key: string }> = {
    ahead: { bg: 'bg-emerald-100 dark:bg-emerald-500/20', text: 'text-emerald-700 dark:text-emerald-400', key: 'goals.onTrackAhead' },
    on_track: { bg: 'bg-blue-100 dark:bg-blue-500/20', text: 'text-blue-700 dark:text-blue-400', key: 'goals.onTrackOnTrack' },
    behind: { bg: 'bg-amber-100 dark:bg-amber-500/20', text: 'text-amber-700 dark:text-amber-400', key: 'goals.onTrackBehind' },
    overdue: { bg: 'bg-rose-100 dark:bg-rose-500/20', text: 'text-rose-700 dark:text-rose-400', key: 'goals.onTrackOverdue' },
    achieved: { bg: 'bg-emerald-100 dark:bg-emerald-500/20', text: 'text-emerald-700 dark:text-emerald-400', key: 'goals.onTrackAchieved' },
  }
  const c = config[status]
  if (!c) return null
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold ${c.bg} ${c.text}`}>
      {t(c.key)}
    </span>
  )
}

function StatusBadge({ status, t }: { status: string; t: (key: string) => string }) {
  const config: Record<string, { bg: string; text: string; key: string }> = {
    active: { bg: 'bg-emerald-100 dark:bg-emerald-500/20', text: 'text-emerald-700 dark:text-emerald-400', key: 'goals.statusActive' },
    completed: { bg: 'bg-blue-100 dark:bg-blue-500/20', text: 'text-blue-700 dark:text-blue-400', key: 'goals.statusCompleted' },
    paused: { bg: 'bg-amber-100 dark:bg-amber-500/20', text: 'text-amber-700 dark:text-amber-400', key: 'goals.statusPaused' },
    archived: { bg: 'bg-muted', text: 'text-muted-foreground', key: 'goals.statusArchived' },
  }
  const c = config[status] ?? config.active
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold ${c.bg} ${c.text}`}>
      {t(c.key)}
    </span>
  )
}

export default function GoalsPage() {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const timeZone = useEffectiveTimezone()
  const today = todayInTimezone(timeZone)
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Goal | null>(null)
  const [trackingType, setTrackingType] = useState('manual')
  const [statusFilter, setStatusFilter] = useState<string>('active')
  const [deletingGoal, setDeletingGoal] = useState<Goal | null>(null)
  const [selectedIcon, setSelectedIcon] = useState('target')
  const [selectedColor, setSelectedColor] = useState('#3B82F6')
  const [targetDate, setTargetDate] = useState('')
  const [pocketAccountId, setPocketAccountId] = useState('')
  const [initialMode, setInitialMode] = useState<'zero' | 'custom' | 'available'>('zero')
  const [initialAmount, setInitialAmount] = useState('')
  const [managingPocket, setManagingPocket] = useState<Goal | null>(null)
  const [adjustmentMode, setAdjustmentMode] = useState<'reserve' | 'release'>('reserve')
  const [adjustmentAmount, setAdjustmentAmount] = useState('')

  const { data: goalsList } = useQuery({
    queryKey: ['goals', statusFilter],
    queryFn: () => goalsApi.list(statusFilter || undefined),
  })
  const { data: allGoals } = useQuery({
    queryKey: ['goals', 'pocket-availability'],
    queryFn: () => goalsApi.list(),
  })

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const { data: assetsList } = useQuery({
    queryKey: ['assets'],
    queryFn: () => assetsApi.list(),
  })

  const { data: walletsList } = useQuery({
    queryKey: ['asset-groups'],
    queryFn: assetGroupsApi.list,
  })

  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currenciesApi.list,
    staleTime: Infinity,
  })

  const createMutation = useMutation({
    mutationFn: (data: Partial<Goal>) => goalsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      setDialogOpen(false)
      toast.success(t('goals.created'))
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Goal> & { id: string }) => goalsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('goals.updated'))
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => goalsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      setDeletingGoal(null)
      toast.success(t('goals.deleted'))
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => goalsApi.update(id, { status } as Partial<Goal>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      toast.success(t('goals.updated'))
    },
  })

  const adjustmentMutation = useMutation({
    mutationFn: ({ id, amount }: { id: string; amount: number }) => goalsApi.adjust(id, amount),
    onSuccess: (goal) => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      setManagingPocket(goal)
      setAdjustmentAmount('')
      toast.success(t('goals.adjusted'))
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const { data: pocketActivity } = useQuery({
    queryKey: ['goals', managingPocket?.id, 'activity'],
    queryFn: () => goalsApi.activity(managingPocket!.id),
    enabled: !!managingPocket,
  })

  const reservedForAccount = (accountId: string) => {
    const snapshot = (allGoals ?? []).find(
      goal => goal.tracking_type === 'pocket' && goal.account_id === accountId,
    )
    if (snapshot?.account_reserved_total != null) return snapshot.account_reserved_total
    return (allGoals ?? [])
      .filter(goal => goal.tracking_type === 'pocket' && goal.account_id === accountId)
      .reduce((sum, goal) => sum + goal.current_amount, 0)
  }

  const selectedPocketAccount = (accountsList ?? []).find(account => account.id === pocketAccountId)
  const selectedAccountAvailable = selectedPocketAccount
    ? selectedPocketAccount.current_balance - reservedForAccount(selectedPocketAccount.id)
    : 0

  const openCreateDialog = () => {
    setEditing(null)
    setTrackingType('manual')
    setSelectedIcon('target')
    setSelectedColor('#3B82F6')
    setTargetDate('')
    setPocketAccountId('')
    setInitialMode('zero')
    setInitialAmount('')
    setDialogOpen(true)
  }

  const openEditDialog = (goal: Goal) => {
    setEditing(goal)
    setTrackingType(goal.tracking_type)
    setSelectedIcon(goal.icon ?? 'target')
    setSelectedColor(goal.color ?? '#3B82F6')
    setTargetDate(goal.target_date ?? '')
    setPocketAccountId(goal.account_id ?? '')
    setInitialMode('zero')
    setInitialAmount('')
    setDialogOpen(true)
  }

  return (
    <div>
      <PageHeader
        section={t('goals.title')}
        title={t('goals.title')}
      />

      {/* Status filter */}
      <div className="flex items-center gap-2 mb-4">
        {['active', 'completed', 'paused', 'archived', ''].map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              statusFilter === s
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:text-foreground'
            }`}
          >
            {s ? t(`goals.status${s.charAt(0).toUpperCase() + s.slice(1)}`) : t('transactions.all')}
          </button>
        ))}
      </div>

      <SectionCard>
        <SectionHeader
          title={t('goals.title')}
          action={
            canWrite ? (
              <Button size="sm" className="gap-1.5 h-8" onClick={openCreateDialog}>
                <Plus size={13} /> {t('goals.add')}
              </Button>
            ) : undefined
          }
        />
        {goalsList && goalsList.length > 0 ? (
          <div className="divide-y divide-border">
            {goalsList.map((goal) => {
              const days = goal.target_date
                ? differenceInCalendarDays(parseISO(goal.target_date), parseISO(today))
                : null
              const progressColor = goal.percentage >= 100
                ? 'bg-emerald-500'
                : goal.percentage >= 60
                  ? 'bg-blue-500'
                  : goal.percentage >= 30
                    ? 'bg-amber-400'
                    : 'bg-muted-foreground/30'

              const GoalIcon = getGoalIcon(goal.icon)
              return (
                <div key={goal.id} className="px-4 sm:px-5 py-4 hover:bg-muted/50 transition-colors">
                  <div className="flex items-start gap-4">
                    {/* Icon */}
                    <div
                      className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 text-white"
                      style={{ backgroundColor: goal.color ?? '#6B7280' }}
                    >
                      <GoalIcon size={18} />
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-semibold text-foreground truncate">{goal.name}</span>
                        <StatusBadge status={goal.status} t={t} />
                        <OnTrackBadge status={goal.on_track} t={t} />
                      </div>

                      {/* Progress bar */}
                      <div className="flex items-center gap-3 mb-1.5">
                        <div className="flex-1 h-2 bg-muted/60 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all ${progressColor}`}
                            style={{ width: `${Math.min(goal.percentage, 100)}%` }}
                          />
                        </div>
                        <span className="text-xs font-bold tabular-nums text-foreground shrink-0">
                          {goal.percentage.toFixed(0)}%
                        </span>
                      </div>

                      {/* Details row */}
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                        <span className="tabular-nums font-medium">
                          {mask(formatCurrency(goal.current_amount, goal.currency, locale))}
                          {' / '}
                          {mask(formatCurrency(goal.target_amount, goal.currency, locale))}
                        </span>
                        {goal.monthly_contribution != null && goal.monthly_contribution > 0 && (
                          <span className="tabular-nums">
                            {mask(formatCurrency(goal.monthly_contribution, goal.currency, locale))}{t('goals.perMonth')}
                          </span>
                        )}
                        {days !== null && (
                          <span className={days < 0 ? 'text-rose-500' : ''}>
                            {days >= 0
                              ? t('goals.daysRemaining', { count: days })
                              : t('goals.daysOverdue', { count: Math.abs(days) })}
                          </span>
                        )}
                        {!goal.target_date && (
                          <span>{t('goals.noTargetDate')}</span>
                        )}
                        {goal.account_name && (
                          <span>{goal.account_name}</span>
                        )}
                        {goal.tracking_type === 'pocket' && goal.account_available != null && (
                          <span>
                            {t('goals.accountAvailable')}: {mask(formatCurrency(goal.account_available, goal.currency, locale))}
                          </span>
                        )}
                        {goal.tracking_type === 'pocket' && goal.is_underfunded && (
                          <span className="inline-flex items-center gap-1 text-rose-600 dark:text-rose-400">
                            <AlertTriangle size={12} /> {t('goals.underfunded')}
                          </span>
                        )}
                        {goal.asset_name && (
                          <span>{goal.asset_name}</span>
                        )}
                        {goal.asset_group_name && (
                          <span>{goal.asset_group_name}</span>
                        )}
                      </div>
                    </div>

                    {/* Actions */}
                    {canWrite && (
                      <div className="flex items-center gap-1 shrink-0">
                        {goal.tracking_type === 'pocket' && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                            onClick={() => {
                              setManagingPocket(goal)
                              setAdjustmentMode(goal.status === 'active' ? 'reserve' : 'release')
                              setAdjustmentAmount('')
                            }}
                            title={t('goals.managePocket')}
                            aria-label={`${t('goals.managePocket')}: ${goal.name}`}
                          >
                            <WalletCards size={13} />
                          </button>
                        )}
                        {goal.status === 'active' && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-amber-500 hover:bg-amber-50 dark:hover:bg-amber-500/10 transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'paused' })}
                            title={t('goals.pause')}
                          >
                            <Pause size={13} />
                          </button>
                        )}
                        {goal.status === 'paused' && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-emerald-500 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'active' })}
                            title={t('goals.resume')}
                          >
                            <Play size={13} />
                          </button>
                        )}
                        {(goal.status === 'active' || goal.status === 'paused') && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-500/10 transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'completed' })}
                            title={t('goals.complete')}
                          >
                            <CheckCircle2 size={13} />
                          </button>
                        )}
                        {goal.status !== 'archived' && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-muted-foreground/80 hover:bg-muted transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'archived' })}
                            title={t('goals.archive')}
                          >
                            <Archive size={13} />
                          </button>
                        )}
                        {(goal.status === 'completed' || goal.status === 'archived') && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-emerald-500 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'active' })}
                            title={t('goals.reactivate')}
                          >
                            <ArchiveRestore size={13} />
                          </button>
                        )}
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                          onClick={() => openEditDialog(goal)}
                          title={t('common.edit')}
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-colors"
                          onClick={() => setDeletingGoal(goal)}
                          title={t('common.delete')}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('goals.empty')}</p>
        )}
      </SectionCard>

      {/* Create/Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={() => { setDialogOpen(false); setEditing(null) }}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? t('goals.edit') : t('goals.add')}</DialogTitle>
            <DialogDescription className="sr-only">
              {trackingType === 'pocket' ? t('goals.pocketHint') : t('goals.title')}
            </DialogDescription>
          </DialogHeader>
          <form
            key={editing?.id ?? 'new'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              const tt = trackingType
              const pocketAccount = (accountsList ?? []).find(account => account.id === pocketAccountId)
              const payload: Record<string, unknown> = {
                name: formData.get('name') as string,
                target_amount: parseFloat(formData.get('target_amount') as string),
                currency: tt === 'pocket'
                  ? (pocketAccount?.currency ?? editing?.currency)
                  : (formData.get('currency') as string) || userCurrency,
                tracking_type: tt,
                target_date: targetDate || null,
                icon: selectedIcon || null,
                color: selectedColor || null,
              }

              if (tt === 'manual') {
                payload.current_amount = parseFloat((formData.get('current_amount') as string) || '0')
              }
              if (tt === 'account') {
                payload.account_id = (formData.get('account_id') as string) || null
              }
              if (tt === 'pocket') {
                payload.account_id = pocketAccountId
                if (!editing) {
                  payload.initial_allocation = initialMode === 'available'
                    ? Math.max(0, selectedAccountAvailable)
                    : initialMode === 'custom'
                      ? parseFloat(initialAmount || '0')
                      : 0
                }
              }
              if (tt === 'asset') {
                payload.asset_id = (formData.get('asset_id') as string) || null
              }
              if (tt === 'asset_group') {
                payload.asset_group_id = (formData.get('asset_group_id') as string) || null
              }

              if (editing) {
                updateMutation.mutate({ id: editing.id, ...payload } as Partial<Goal> & { id: string })
              } else {
                createMutation.mutate(payload as Partial<Goal>)
              }
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label>{t('goals.name')}</Label>
              <Input name="name" defaultValue={editing?.name ?? ''} required />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t('goals.targetAmount')}</Label>
                <Input
                  name="target_amount"
                  type="number"
                  step="0.01"
                  defaultValue={editing?.target_amount?.toString() ?? ''}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label>{t('goals.currency')}</Label>
                {trackingType === 'pocket' ? (
                  <Input
                    value={selectedPocketAccount?.currency ?? editing?.currency ?? ''}
                    readOnly
                    aria-label={t('goals.currency')}
                  />
                ) : (
                  <select
                    name="currency"
                    defaultValue={editing?.currency ?? userCurrency}
                    className={SELECT_CLASS}
                  >
                    {supportedCurrencies?.map((c: { code: string; name: string; flag: string }) => (
                      <option key={c.code} value={c.code}>
                        {c.flag} {c.name} ({c.code})
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>

            <div className="space-y-2">
              <Label>{t('goals.targetDate')}</Label>
              <DatePickerInput
                value={targetDate}
                onChange={setTargetDate}
                className="w-full justify-start"
              />
            </div>

            <div className="space-y-2">
              <Label>{t('goals.trackingType')}</Label>
              <select
                name="tracking_type"
                value={trackingType}
                onChange={(e) => setTrackingType(e.target.value)}
                className={SELECT_CLASS}
                disabled={editing?.tracking_type === 'pocket'}
              >
                <option value="manual">{t('goals.trackingManual')}</option>
                <option value="account">{t('goals.trackingAccount')}</option>
                <option value="asset">{t('goals.trackingAsset')}</option>
                <option value="asset_group">{t('goals.trackingWallet')}</option>
                <option value="net_worth">{t('goals.trackingNetWorth')}</option>
                <option
                  value="pocket"
                  disabled={!!editing && editing.tracking_type !== 'pocket'}
                >
                  {t('goals.trackingPocket')}
                </option>
              </select>
            </div>

            {trackingType === 'manual' && (
              <div className="space-y-2">
                <Label>{t('goals.currentAmount')}</Label>
                <Input
                  name="current_amount"
                  type="number"
                  step="0.01"
                  defaultValue={editing?.tracking_type === 'manual' ? editing?.current_amount?.toString() : '0'}
                />
              </div>
            )}

            {trackingType === 'account' && (
              <LinkedResourceSelect<Account>
                name="account_id"
                label={t('goals.account')}
                placeholder={t('goals.selectAccount')}
                defaultValue={editing?.account_id}
                items={sortAccountsByDisplayName(accountsList ?? [])}
                renderOption={(acc) => `${getAccountName(acc)} (${acc.currency})`}
              />
            )}

            {trackingType === 'pocket' && (
              <div className="space-y-3 rounded-lg border border-border p-3">
                <div className="space-y-2">
                  <Label>{t('goals.account')}</Label>
                  <select
                    value={pocketAccountId}
                    onChange={(event) => {
                      setPocketAccountId(event.target.value)
                      setInitialAmount('')
                    }}
                    className={SELECT_CLASS}
                    required
                    disabled={!!editing}
                  >
                    <option value="">{t('goals.selectAccount')}</option>
                    {sortAccountsByDisplayName((accountsList ?? []).filter(account => account.type !== 'credit_card')).map(account => (
                      <option key={account.id} value={account.id}>
                        {getAccountName(account)} ({account.currency})
                      </option>
                    ))}
                  </select>
                </div>
                {selectedPocketAccount && (
                  <div className="grid grid-cols-1 gap-2 text-xs text-muted-foreground sm:grid-cols-2 sm:gap-3">
                    <span>{t('goals.accountBalance')}: {formatCurrency(selectedPocketAccount.current_balance, selectedPocketAccount.currency, locale)}</span>
                    <span>{t('goals.accountAvailable')}: {formatCurrency(selectedAccountAvailable, selectedPocketAccount.currency, locale)}</span>
                  </div>
                )}
                {!editing && (
                  <div className="space-y-2">
                    <Label>{t('goals.startingAllocation')}</Label>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                      {(['zero', 'custom', 'available'] as const).map(mode => (
                        <button
                          key={mode}
                          type="button"
                          onClick={() => setInitialMode(mode)}
                          aria-pressed={initialMode === mode}
                          className={`rounded-md border px-2 py-2 text-xs font-medium ${initialMode === mode ? 'border-primary bg-primary/5 text-primary' : 'border-border text-muted-foreground'}`}
                        >
                          {t(`goals.starting${mode.charAt(0).toUpperCase() + mode.slice(1)}`)}
                        </button>
                      ))}
                    </div>
                    {initialMode === 'custom' && (
                      <Input
                        type="number"
                        min="0"
                        max={Math.max(0, selectedAccountAvailable)}
                        step="0.01"
                        value={initialAmount}
                        onChange={event => setInitialAmount(event.target.value)}
                        required
                      />
                    )}
                    <p className="text-xs text-muted-foreground">{t('goals.pocketHint')}</p>
                  </div>
                )}
              </div>
            )}

            {trackingType === 'asset' && (
              <LinkedResourceSelect<Asset>
                name="asset_id"
                label={t('goals.asset')}
                placeholder={t('goals.selectAsset')}
                defaultValue={editing?.asset_id}
                items={assetsList}
                renderOption={(asset) => `${asset.name} (${asset.currency})`}
              />
            )}

            {trackingType === 'asset_group' && (
              <LinkedResourceSelect<AssetGroup>
                name="asset_group_id"
                label={t('goals.wallet')}
                placeholder={t('goals.selectWallet')}
                defaultValue={editing?.asset_group_id}
                items={walletsList}
                renderOption={(wallet) => wallet.name}
                hint={t('goals.walletHint')}
              />
            )}

            {/* Icon & Color */}
            <div className="space-y-2">
              <Label>{t('goals.icon')}</Label>
              <Popover>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className="w-full flex items-center gap-3 border border-border rounded-lg px-3 py-2 text-sm bg-card hover:bg-muted/50 transition-colors text-left"
                  >
                    <div
                      className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 text-white"
                      style={{ backgroundColor: selectedColor }}
                    >
                      {createElement(getGoalIcon(selectedIcon), { size: 18 })}
                    </div>
                    <span className="flex-1 text-muted-foreground">{t('goals.chooseIconColor')}</span>
                    <ChevronDown size={14} className="text-muted-foreground" />
                  </button>
                </PopoverTrigger>
                <PopoverContent align="start" className="w-80 p-3 space-y-3">
                  {/* Color presets */}
                  <div className="flex items-center gap-1.5">
                    {PRESET_COLORS.map((c) => (
                      <button
                        key={c}
                        type="button"
                        aria-label={c}
                        title={c}
                        onClick={() => setSelectedColor(c)}
                        className={`w-7 h-7 rounded-full transition-all ${
                          selectedColor === c ? 'ring-2 ring-offset-1 ring-primary scale-110' : 'hover:scale-110'
                        }`}
                        style={{ backgroundColor: c }}
                      />
                    ))}
                    <input
                      type="color"
                      value={selectedColor}
                      onChange={(e) => setSelectedColor(e.target.value)}
                      className="w-7 h-7 rounded-full cursor-pointer border-0 p-0"
                    />
                  </div>
                  {/* Icon picker */}
                  <IconPicker value={selectedIcon} color={selectedColor} onChange={setSelectedIcon} />
                </PopoverContent>
              </Popover>
            </div>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => { setDialogOpen(false); setEditing(null) }}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={createMutation.isPending || updateMutation.isPending}>
                {t('common.save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={!!managingPocket} onOpenChange={() => setManagingPocket(null)}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('goals.managePocket')}: {managingPocket?.name}</DialogTitle>
            <DialogDescription className="sr-only">{t('goals.pocketHint')}</DialogDescription>
          </DialogHeader>
          {managingPocket && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-muted/25 p-3 text-sm sm:grid-cols-3">
                <div>
                  <div className="text-xs text-muted-foreground">{t('goals.reserved')}</div>
                  <div className="font-semibold tabular-nums">{mask(formatCurrency(managingPocket.current_amount, managingPocket.currency, locale))}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">{t('goals.accountBalance')}</div>
                  <div className="font-semibold tabular-nums">{mask(formatCurrency(managingPocket.account_balance ?? 0, managingPocket.currency, locale))}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">{t('goals.accountAvailable')}</div>
                  <div className="font-semibold tabular-nums">{mask(formatCurrency(managingPocket.account_available ?? 0, managingPocket.currency, locale))}</div>
                </div>
              </div>

              {managingPocket.is_underfunded && (
                <div className="flex items-start gap-2 rounded-md bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                  <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                  {t('goals.underfundedHint')}
                </div>
              )}

              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <Button
                    type="button"
                    variant={adjustmentMode === 'reserve' ? 'default' : 'outline'}
                    disabled={managingPocket.status !== 'active'}
                    onClick={() => { setAdjustmentMode('reserve'); setAdjustmentAmount('') }}
                  >
                    {t('goals.reserve')}
                  </Button>
                  <Button
                    type="button"
                    variant={adjustmentMode === 'release' ? 'default' : 'outline'}
                    onClick={() => { setAdjustmentMode('release'); setAdjustmentAmount('') }}
                  >
                    {t('goals.release')}
                  </Button>
                </div>
                <Input
                  type="number"
                  min="0.01"
                  max={adjustmentMode === 'reserve'
                    ? Math.max(0, managingPocket.account_available ?? 0)
                    : managingPocket.current_amount}
                  step="0.01"
                  value={adjustmentAmount}
                  onChange={event => setAdjustmentAmount(event.target.value)}
                  placeholder={t('goals.adjustmentAmount')}
                />
                <Button
                  type="button"
                  className="w-full"
                  disabled={adjustmentMutation.isPending || !adjustmentAmount || Number(adjustmentAmount) <= 0}
                  onClick={() => adjustmentMutation.mutate({
                    id: managingPocket.id,
                    amount: (adjustmentMode === 'release' ? -1 : 1) * Number(adjustmentAmount),
                  })}
                >
                  {adjustmentMutation.isPending ? t('common.loading') : t('common.save')}
                </Button>
              </div>

              <div className="space-y-2">
                <Label>{t('goals.activity')}</Label>
                <div className="max-h-48 divide-y divide-border overflow-y-auto rounded-md border border-border">
                  {pocketActivity && pocketActivity.length > 0 ? pocketActivity.map(entry => (
                    <div key={entry.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                      <div>
                        <div className="font-medium">{t(`goals.activity${entry.source.charAt(0).toUpperCase() + entry.source.slice(1)}`)}</div>
                        <div className="text-xs text-muted-foreground">{new Date(entry.created_at).toLocaleDateString(dateLocale, { timeZone })}</div>
                      </div>
                      <div className={`font-semibold tabular-nums ${entry.amount < 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                        {entry.amount > 0 ? '+' : ''}{mask(formatCurrency(entry.amount, managingPocket.currency, locale))}
                      </div>
                    </div>
                  )) : (
                    <div className="px-3 py-6 text-center text-sm text-muted-foreground">{t('goals.noActivity')}</div>
                  )}
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Confirm delete dialog */}
      <Dialog open={!!deletingGoal} onOpenChange={() => setDeletingGoal(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('goals.confirmDeleteTitle')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t(
              deletingGoal?.tracking_type === 'pocket'
                ? 'goals.confirmDeletePocketDesc'
                : 'goals.confirmDeleteDesc',
              { name: deletingGoal?.name },
            )}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeletingGoal(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => deletingGoal && deleteMutation.mutate(deletingGoal.id)}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? t('common.loading') : t('common.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
