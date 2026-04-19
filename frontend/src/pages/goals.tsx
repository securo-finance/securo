import { createElement, useState } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { goals as goalsApi, accounts as accountsApi, assets as assetsApi, currencies as currenciesApi } from '@/lib/api'
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
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from '@/components/ui/popover'
import type { Goal } from '@/types'
import {
  Pencil, Trash2, Plus, Pause, Play, CheckCircle2, Archive, Target,
  ChevronDown,
} from 'lucide-react'
import { ICON_MAP } from '@/lib/category-icons'
import { IconPicker } from '@/components/icon-picker'
import { PageHeader } from '@/components/page-header'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

function getGoalIcon(iconKey: string | null) {
  return (iconKey && ICON_MAP[iconKey]) || Target
}

const PRESET_COLORS = [
  '#3B82F6', '#10B981', '#F59E0B', '#EF4444',
  '#8B5CF6', '#EC4899', '#06B6D4', '#F97316',
]

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

function daysUntil(dateStr: string): number {
  const target = new Date(dateStr + 'T00:00:00')
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.ceil((target.getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

export default function GoalsPage() {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Goal | null>(null)
  const [trackingType, setTrackingType] = useState('manual')
  const [statusFilter, setStatusFilter] = useState<string>('active')
  const [selectedIcon, setSelectedIcon] = useState('target')
  const [selectedColor, setSelectedColor] = useState('#3B82F6')

  const { data: goalsList } = useQuery({
    queryKey: ['goals', statusFilter],
    queryFn: () => goalsApi.list(statusFilter || undefined),
  })

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const { data: assetsList } = useQuery({
    queryKey: ['assets'],
    queryFn: () => assetsApi.list(),
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
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Goal> & { id: string }) => goalsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('goals.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => goalsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      toast.success(t('goals.deleted'))
    },
  })

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => goalsApi.update(id, { status } as Partial<Goal>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      toast.success(t('goals.updated'))
    },
  })

  const openCreateDialog = () => {
    setEditing(null)
    setTrackingType('manual')
    setSelectedIcon('target')
    setSelectedColor('#3B82F6')
    setDialogOpen(true)
  }

  const openEditDialog = (goal: Goal) => {
    setEditing(goal)
    setTrackingType(goal.tracking_type)
    setSelectedIcon(goal.icon ?? 'target')
    setSelectedColor(goal.color ?? '#3B82F6')
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
            <Button size="sm" className="gap-1.5 h-8" onClick={openCreateDialog}>
              <Plus size={13} /> {t('goals.add')}
            </Button>
          }
        />
        {goalsList && goalsList.length > 0 ? (
          <div className="divide-y divide-border">
            {goalsList.map((goal) => {
              const days = goal.target_date ? daysUntil(goal.target_date) : null
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
                        {goal.asset_name && (
                          <span>{goal.asset_name}</span>
                        )}
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1 shrink-0">
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
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                        onClick={() => openEditDialog(goal)}
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-colors"
                        onClick={() => deleteMutation.mutate(goal.id)}
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
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
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t('goals.edit') : t('goals.add')}</DialogTitle>
          </DialogHeader>
          <form
            key={editing?.id ?? 'new'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              const payload: Record<string, unknown> = {
                name: formData.get('name') as string,
                target_amount: parseFloat(formData.get('target_amount') as string),
                currency: (formData.get('currency') as string) || userCurrency,
                tracking_type: formData.get('tracking_type') as string,
                target_date: (formData.get('target_date') as string) || null,
                icon: selectedIcon || null,
                color: selectedColor || null,
              }

              const tt = formData.get('tracking_type') as string
              if (tt === 'manual') {
                payload.current_amount = parseFloat((formData.get('current_amount') as string) || '0')
              }
              if (tt === 'account') {
                payload.account_id = (formData.get('account_id') as string) || null
              }
              if (tt === 'asset') {
                payload.asset_id = (formData.get('asset_id') as string) || null
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
                <select
                  name="currency"
                  defaultValue={editing?.currency ?? userCurrency}
                  className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  {supportedCurrencies?.map((c: { code: string; name: string; flag: string }) => (
                    <option key={c.code} value={c.code}>
                      {c.flag} {c.name} ({c.code})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="space-y-2">
              <Label>{t('goals.targetDate')}</Label>
              <Input
                name="target_date"
                type="date"
                defaultValue={editing?.target_date ?? ''}
              />
            </div>

            <div className="space-y-2">
              <Label>{t('goals.trackingType')}</Label>
              <select
                name="tracking_type"
                value={trackingType}
                onChange={(e) => setTrackingType(e.target.value)}
                className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="manual">{t('goals.trackingManual')}</option>
                <option value="account">{t('goals.trackingAccount')}</option>
                <option value="asset">{t('goals.trackingAsset')}</option>
                <option value="net_worth">{t('goals.trackingNetWorth')}</option>
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
              <div className="space-y-2">
                <Label>{t('goals.account')}</Label>
                <select
                  name="account_id"
                  defaultValue={editing?.account_id ?? ''}
                  className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                  required
                >
                  <option value="">{t('goals.selectAccount')}</option>
                  {accountsList?.map((acc) => (
                    <option key={acc.id} value={acc.id}>
                      {getAccountName(acc)} ({acc.currency})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {trackingType === 'asset' && (
              <div className="space-y-2">
                <Label>{t('goals.asset')}</Label>
                <select
                  name="asset_id"
                  defaultValue={editing?.asset_id ?? ''}
                  className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                  required
                >
                  <option value="">{t('goals.selectAsset')}</option>
                  {assetsList?.map((asset: { id: string; name: string; currency: string }) => (
                    <option key={asset.id} value={asset.id}>
                      {asset.name} ({asset.currency})
                    </option>
                  ))}
                </select>
              </div>
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
    </div>
  )
}
