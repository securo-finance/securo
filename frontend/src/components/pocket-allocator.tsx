import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Search } from 'lucide-react'
import { differenceInCalendarDays, parseISO } from 'date-fns'
import { goals as goalsApi } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { todayInTimezone } from '@/lib/date-utils'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { useEffectiveTimezone } from '@/hooks/use-timezone'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import type { Goal, GoalAllocation, GoalAllocationInput } from '@/types'

const EMPTY_ORIGINAL_ALLOCATIONS: Pick<GoalAllocation, 'goal_id' | 'amount'>[] = []

function allocationTotal(value: GoalAllocationInput[]): number {
  return value.reduce((sum, item) => sum + Math.round(item.amount * 100), 0) / 100
}

function GoalStatus({ status }: { status: Goal['on_track'] }) {
  const { t } = useTranslation()
  if (!status) return null
  const translationKeys: Record<NonNullable<Goal['on_track']>, string> = {
    ahead: 'goals.onTrackAhead',
    on_track: 'goals.onTrackOnTrack',
    behind: 'goals.onTrackBehind',
    overdue: 'goals.onTrackOverdue',
    achieved: 'goals.onTrackAchieved',
  }
  return (
    <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
      {t(translationKeys[status])}
    </span>
  )
}

function GoalTiming({ goal }: { goal: Goal }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const timeZone = useEffectiveTimezone()
  const days = goal.target_date
    ? differenceInCalendarDays(parseISO(goal.target_date), parseISO(todayInTimezone(timeZone)))
    : null

  return (
    <span className="inline-flex flex-wrap justify-end gap-x-2">
      {goal.monthly_contribution != null && goal.monthly_contribution > 0 && (
        <span>
          {formatCurrency(goal.monthly_contribution, goal.currency, locale)}{t('goals.perMonth')}
        </span>
      )}
      {days !== null ? (
        <span className={days < 0 ? 'text-rose-500' : undefined}>
          {days >= 0
            ? t('goals.daysRemaining', { count: days })
            : t('goals.daysOverdue', { count: Math.abs(days) })}
        </span>
      ) : (
        <span>{t('goals.noTargetDate')}</span>
      )}
    </span>
  )
}

export function PocketAllocator({
  accountId,
  transactionType,
  transactionAmount,
  transactionStatus,
  isIgnored = false,
  value,
  originalAllocations = EMPTY_ORIGINAL_ALLOCATIONS,
  onChange,
}: {
  accountId: string
  transactionType: 'debit' | 'credit'
  transactionAmount: number
  transactionStatus: 'posted' | 'pending'
  isIgnored?: boolean
  value: GoalAllocationInput[]
  originalAllocations?: Pick<GoalAllocation, 'goal_id' | 'amount'>[]
  onChange: (value: GoalAllocationInput[]) => void
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const originalAmounts = useMemo(
    () => new Map(originalAllocations.map(item => [item.goal_id, Number(item.amount)])),
    [originalAllocations],
  )
  const [dialogOpen, setDialogOpen] = useState(false)
  const [draft, setDraft] = useState<GoalAllocationInput[]>([])
  const [search, setSearch] = useState('')
  const { data: allGoals } = useQuery({
    queryKey: ['goals', 'pocket-allocator'],
    queryFn: () => goalsApi.list(),
  })
  const existingIds = useMemo(
    () => new Set([...value, ...originalAllocations].map(item => item.goal_id)),
    [value, originalAllocations],
  )
  const pockets = useMemo(
    () => (allGoals ?? []).filter(goal => {
      if (goal.tracking_type !== 'pocket' || goal.account_id !== accountId) return false
      if (existingIds.has(goal.id)) return true
      if (transactionType === 'credit') return goal.status === 'active'
      return goal.status !== 'archived' && goal.current_amount > 0
    }),
    [accountId, allGoals, existingIds, transactionType],
  )
  const filteredPockets = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase(locale)
    if (!normalizedSearch) return pockets
    return pockets.filter(goal => goal.name.toLocaleLowerCase(locale).includes(normalizedSearch))
  }, [locale, pockets, search])

  if (!accountId || pockets.length === 0) return null

  const total = allocationTotal(value)
  const remaining = Math.max(0, Math.abs(transactionAmount) - total)
  const draftTotal = allocationTotal(draft)
  const draftRemaining = Math.max(0, Math.abs(transactionAmount) - draftTotal)
  const disabled = isIgnored || transactionStatus !== 'posted' || transactionAmount <= 0
  const draftExceedsTransaction = draftTotal > Math.abs(transactionAmount)
  const maxAllocationForGoal = (goal: Goal) => disabled
    ? 0
    : transactionType === 'debit'
      ? Math.max(0, Math.round((goal.current_amount - (originalAmounts.get(goal.id) ?? 0)) * 100) / 100)
      : Math.abs(transactionAmount)
  const draftExceedsPocket = transactionType === 'debit' && draft.some(item => {
    const goal = pockets.find(candidate => candidate.id === item.goal_id)
    return goal != null && item.amount > maxAllocationForGoal(goal)
  })
  const draftIsInvalid = (disabled && draft.length > 0) || draftExceedsTransaction || draftExceedsPocket
  const selectedPockets = pockets.filter(goal => value.some(item => item.goal_id === goal.id))
  const allocationHint = isIgnored
    ? 'goals.ignoredHint'
    : transactionStatus !== 'posted'
      ? 'goals.postedOnlyHint'
      : transactionType === 'credit' ? 'goals.depositHint' : 'goals.withdrawalHint'

  const closeDialog = () => {
    setDraft(value.map(item => ({ ...item })))
    setDialogOpen(false)
  }

  const handleDialogOpenChange = (open: boolean) => {
    if (open) {
      setDraft(value.map(item => ({ ...item })))
      setSearch('')
      setDialogOpen(true)
      return
    }
    closeDialog()
  }

  const setAmount = (goalId: string, raw: string) => {
    const amount = Number(raw)
    const next = draft.filter(item => item.goal_id !== goalId)
    if (Number.isFinite(amount) && amount > 0) next.push({ goal_id: goalId, amount })
    setDraft(next)
  }

  const applyDraft = () => {
    if (draftIsInvalid) return
    onChange(draft)
    setDialogOpen(false)
  }

  const currency = pockets[0]?.currency

  return (
    <Dialog open={dialogOpen} onOpenChange={handleDialogOpenChange}>
      <section className="rounded-lg border border-border bg-muted/25 p-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <p className="text-sm font-medium text-foreground">{t('goals.transactionAllocation')}</p>
              {value.length > 0 && (
                <span className="text-xs tabular-nums text-muted-foreground">
                  {t('goals.selectedPockets', { count: value.length })}
                </span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t(allocationHint)}
            </p>
            {selectedPockets.length > 0 && (
              <p className="mt-1 truncate text-xs text-muted-foreground">
                {selectedPockets.slice(0, 2).map(goal => goal.name).join(', ')}
                {selectedPockets.length > 2
                  ? ` ${t('goals.morePockets', { count: selectedPockets.length - 2 })}`
                  : ''}
              </p>
            )}
          </div>

          <div className="flex shrink-0 items-center justify-between gap-3 sm:justify-end">
            <div className="text-right text-xs tabular-nums text-muted-foreground">
              <div>{t('goals.allocated')}: {formatCurrency(total, currency, locale)}</div>
              <div>{t('goals.remaining')}: {formatCurrency(remaining, currency, locale)}</div>
            </div>
            <DialogTrigger asChild>
              <Button type="button" variant="outline" size="sm" disabled={disabled && value.length === 0}>
                {t(value.length > 0 ? 'goals.editPockets' : 'goals.choosePockets')}
              </Button>
            </DialogTrigger>
          </div>
        </div>

        {total > Math.abs(transactionAmount) && (
          <p className="mt-2 text-xs text-destructive">{t('goals.allocationExceedsTransaction')}</p>
        )}

        <DialogContent className="flex h-[min(50rem,calc(100dvh-2rem))] grid-rows-none flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl">
          <DialogHeader className="border-b border-border px-5 py-4 pr-12 sm:px-6">
            <DialogTitle>{t('goals.transactionAllocation')}</DialogTitle>
            <DialogDescription>
              {t(allocationHint)}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 border-b border-border px-5 py-4 sm:px-6">
            <div className="relative">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                type="search"
                value={search}
                onChange={event => setSearch(event.target.value)}
                placeholder={t('goals.searchPockets')}
                aria-label={t('goals.searchPockets')}
                className="pl-9"
              />
            </div>
            <div className="grid grid-cols-2 divide-x divide-border rounded-lg border border-border bg-muted/25">
              <div className="px-4 py-3">
                <div className="text-xs text-muted-foreground">{t('goals.allocated')}</div>
                <div className="mt-0.5 text-lg font-semibold tabular-nums">
                  {formatCurrency(draftTotal, currency, locale)}
                </div>
              </div>
              <div className="px-4 py-3">
                <div className="text-xs text-muted-foreground">{t('goals.remaining')}</div>
                <div className="mt-0.5 text-lg font-semibold tabular-nums">
                  {formatCurrency(draftRemaining, currency, locale)}
                </div>
              </div>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3 sm:px-6">
            {filteredPockets.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {t('goals.noPocketsFound')}
              </p>
            ) : (
              <div className="divide-y divide-border rounded-lg border border-border">
                {filteredPockets.map(goal => {
                  const assigned = draft.find(item => item.goal_id === goal.id)?.amount ?? 0
                  const maxForGoal = maxAllocationForGoal(goal)
                  return (
                    <div
                      key={goal.id}
                      className="grid gap-3 p-3 sm:grid-cols-[minmax(0,1fr)_9rem] sm:items-center sm:p-4"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex min-w-0 items-center gap-2">
                            <div className="truncate text-sm font-semibold text-foreground">{goal.name}</div>
                            <GoalStatus status={goal.on_track} />
                          </div>
                          <div className="shrink-0 text-xs font-semibold tabular-nums text-foreground">
                            {goal.percentage.toFixed(0)}%
                          </div>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-muted-foreground">
                          <span className="tabular-nums">
                            {formatCurrency(goal.current_amount, goal.currency, locale)}
                            {' / '}
                            {formatCurrency(goal.target_amount, goal.currency, locale)}
                          </span>
                          <GoalTiming goal={goal} />
                        </div>
                        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-primary"
                            style={{ width: `${Math.min(Math.max(goal.percentage, 0), 100)}%` }}
                          />
                        </div>
                      </div>
                      <Input
                        aria-label={t('goals.allocationFor', { name: goal.name })}
                        type="number"
                        inputMode="decimal"
                        min="0"
                        max={maxForGoal}
                        step="0.01"
                        value={assigned || ''}
                        disabled={disabled && assigned === 0}
                        aria-invalid={assigned > maxForGoal || undefined}
                        onChange={event => setAmount(goal.id, event.target.value)}
                      />
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          <div className="border-t border-border px-5 py-4 sm:px-6">
            {draftExceedsTransaction && (
              <p className="mb-3 text-xs text-destructive">{t('goals.allocationExceedsTransaction')}</p>
            )}
            {draftExceedsPocket && (
              <p className="mb-3 text-xs text-destructive">{t('goals.allocationExceedsPocket')}</p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={closeDialog}>
                {t('common.cancel')}
              </Button>
              <Button type="button" onClick={applyDraft} disabled={draftIsInvalid}>
                {t('goals.applyAllocation')}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </section>
    </Dialog>
  )
}
