import { useDeferredValue, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Calculator, Pencil, Plus, X } from 'lucide-react'
import {
  Area,
  AreaChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { accounts as accountsApi, spendingPlan as spendingPlanApi } from '@/lib/api'
import { getAccountName, sortAccountsByDisplayName } from '@/lib/account-utils'
import { extractApiError } from '@/lib/api-errors'
import { formatCompactCurrency, formatCurrency } from '@/lib/format'
import { balanceOn, coverageLabel, defaultSavingsDate, formatPlanDate, lowestBalance } from '@/lib/spending-plan'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/page-header'
import { AccountIcon } from '@/components/account-icon'
import { Button } from '@/components/ui/button'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { useWorkspace } from '@/contexts/workspace-context'
import type { SimulatedPurchase, SpendingPlan, SpendingPlanInput, SpendingPlanTimeline } from '@/types'

const SELECT_CLASS = 'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'
const TH = 'text-xs font-medium text-muted-foreground py-3'
const HORIZONS = [6, 12, 18, 24, 36]
const BALANCE = '#10B981'
const SCENARIO = '#F59E0B'
const FLOOR = '#F43F5E'
const TOOLTIP_STYLE = {
  background: 'var(--card)',
  color: 'var(--foreground)',
  border: '1px solid var(--border)',
  borderRadius: '0.75rem',
  boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
  fontSize: '12px',
}

function parseAmount(value: string) {
  const parsed = Number(value.replace(',', '.'))
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0
}

function SectionCard({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('bg-card rounded-xl border border-border shadow-sm overflow-hidden', className)}>
      {children}
    </div>
  )
}

function SectionHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

export default function SpendingPlanPage() {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const { canWrite } = useWorkspace()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()

  const { data: plan, isLoading: planLoading } = useQuery({
    queryKey: ['spending-plan'],
    queryFn: spendingPlanApi.get,
  })
  const { data: timeline } = useQuery({
    queryKey: ['dashboard', 'spending-plan', 'timeline', null],
    queryFn: () => spendingPlanApi.timeline(),
    enabled: !planLoading,
  })

  const [editorOpen, setEditorOpen] = useState(false)
  const [simulated, setSimulated] = useState<SimulatedPurchase | null>(null)
  const { data: scenario } = useQuery({
    queryKey: ['dashboard', 'spending-plan', 'simulate', simulated],
    queryFn: () => spendingPlanApi.simulate([simulated as SimulatedPurchase]),
    enabled: simulated !== null,
    placeholderData: (previous) => previous,
  })
  const activeScenario = simulated ? scenario : undefined
  const [savingsDate, setSavingsDate] = useState('')

  const currency = timeline?.currency ?? 'BRL'
  const money = (value: number) => mask(formatCurrency(value, currency, locale))
  const today = timeline?.points[0]?.date ?? ''
  const savingsTarget = timeline
    ? (savingsDate && savingsDate >= today && savingsDate <= timeline.horizon_end
      ? savingsDate
      : defaultSavingsDate(today, timeline.horizon_end))
    : ''

  const editButton = canWrite ? (
    <Button size="sm" variant={plan ? 'outline' : 'default'} className="gap-1.5 h-8" onClick={() => setEditorOpen(true)}>
      {plan ? <Pencil size={13} /> : <Plus size={13} />}
      {plan ? t('spendingPlan.editPlan') : t('spendingPlan.createPlan')}
    </Button>
  ) : undefined

  return (
    <div>
      <PageHeader section={t('spendingPlan.title')} title={t('spendingPlan.title')} action={editButton} />

      {planLoading || !timeline ? (
        <Skeleton className="h-40 w-full rounded-xl mb-5" />
      ) : plan ? (
        <PlanHero plan={plan} timeline={timeline} money={money} dateLocale={dateLocale} />
      ) : (
        <SectionCard className="mb-5">
          <div className="px-5 py-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div className="max-w-2xl space-y-1">
              <p className="text-sm font-semibold text-foreground">{t('spendingPlan.noPlan')}</p>
              <p className="text-sm text-muted-foreground">{t('spendingPlan.intro')}</p>
              <p className="text-sm text-foreground">
                {t('spendingPlan.noPlanSuggestion', {
                  surplus: money(timeline.average_daily_surplus),
                  amount: money(timeline.safe_daily_allowance),
                  date: formatPlanDate(timeline.horizon_end, dateLocale),
                })}
              </p>
            </div>
            {editButton}
          </div>
        </SectionCard>
      )}

      <SectionCard className="mb-5">
        <SectionHeader
          title={t('spendingPlan.chartTitle')}
          action={timeline ? (
            <ChartLegend
              floor={plan ? money(timeline.balance_floor) : null}
              scenario={!!activeScenario}
            />
          ) : undefined}
        />
        <div className="h-72 px-2 sm:px-3 pt-4 pb-2">
          {timeline ? (
            <PlanChart
              timeline={timeline}
              scenario={activeScenario}
              showFloor={!!plan}
              currency={currency}
              locale={locale}
              dateLocale={dateLocale}
              money={money}
            />
          ) : (
            <Skeleton className="h-full w-full" />
          )}
        </div>
      </SectionCard>

      {timeline && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] items-start">
          <PurchaseSimulator
            timeline={timeline}
            scenario={activeScenario}
            simulated={simulated}
            onSimulate={setSimulated}
            savingsTarget={savingsTarget}
            money={money}
            dateLocale={dateLocale}
          />
          <SectionCard>
            <SectionHeader
              title={t('spendingPlan.savingsTitle')}
              subtitle={t('spendingPlan.savingsHint')}
            />
            <div className="px-4 sm:px-5 py-4 space-y-4">
              <div className="space-y-2">
                <Label>{t('spendingPlan.savingsUntil')}</Label>
                <DatePickerInput
                  value={savingsTarget}
                  onChange={(value) => setSavingsDate(value)}
                  className="w-full justify-start"
                />
              </div>
              <SavingsFigure
                label={activeScenario ? t('spendingPlan.withoutPurchase') : null}
                timeline={timeline}
                target={savingsTarget}
                money={money}
              />
              {activeScenario && (
                <SavingsFigure
                  label={t('spendingPlan.withPurchase')}
                  timeline={activeScenario}
                  target={savingsTarget}
                  money={money}
                  tone="scenario"
                />
              )}
            </div>
          </SectionCard>
        </div>
      )}

      {editorOpen && (
        <PlanEditorDialog plan={plan ?? null} onClose={() => setEditorOpen(false)} />
      )}
    </div>
  )
}

/** Mirrors the dashboard's hero card: one headline figure, then a row of secondary ones. */
function PlanHero({
  plan,
  timeline,
  money,
  dateLocale,
}: {
  plan: SpendingPlan
  timeline: SpendingPlanTimeline
  money: (value: number) => string
  dateLocale: string
}) {
  const { t } = useTranslation()
  const remaining = timeline.today.remaining
  const covered = timeline.covered_until !== null
  const divider = "relative min-w-0 sm:before:content-[''] sm:before:absolute sm:before:-left-2.5 sm:before:top-1.5 sm:before:bottom-1.5 sm:before:w-px sm:before:bg-border"
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm mb-5 px-5 pt-5 pb-4">
      <div className="pb-4 mb-4 border-b border-border">
        <p className="text-xs font-semibold text-muted-foreground mb-1">{t('spendingPlan.todayLabel')}</p>
        <p className={cn('text-3xl font-bold tabular-nums', remaining < 0 ? 'text-rose-500' : 'text-foreground')}>
          {money(remaining)}
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          {t('spendingPlan.todayCaption', { spent: money(timeline.today.spent), allowance: money(timeline.today.allowance) })}
        </p>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-5 gap-y-4">
        <Indicator label={t('spendingPlan.dailyAllowance')} value={money(Number(plan.daily_allowance))} />
        <Indicator
          className={divider}
          label={t('spendingPlan.surplusLabel')}
          value={money(timeline.average_daily_surplus)}
          caption={t('spendingPlan.surplusCaption')}
          tone={timeline.average_daily_surplus < 0 ? 'bad' : 'neutral'}
        />
        <Indicator
          className={divider}
          label={t('spendingPlan.safeLabel')}
          value={money(timeline.safe_daily_allowance)}
          caption={t('spendingPlan.safeCaption', { date: formatPlanDate(timeline.horizon_end, dateLocale) })}
        />
        <Indicator
          className={divider}
          label={t('spendingPlan.coverageLabel')}
          value={timeline.covered_until
            ? formatPlanDate(timeline.covered_until, dateLocale)
            : t('spendingPlan.notCovered')}
          caption={t('spendingPlan.floorCaption', {
            floor: money(timeline.balance_floor),
            horizon: t('spendingPlan.months', { count: plan.horizon_months }),
          })}
          tone={covered ? 'good' : 'bad'}
        />
      </div>
    </div>
  )
}

function Indicator({
  label,
  value,
  caption,
  tone = 'neutral',
  className,
}: {
  label: string
  value: string
  caption?: string
  tone?: 'good' | 'bad' | 'neutral'
  className?: string
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <p className="text-xs font-medium text-muted-foreground mb-1 min-h-[16px]">{label}</p>
      <p className={cn(
        'text-lg sm:text-xl font-bold tabular-nums truncate',
        tone === 'good' && 'text-emerald-600 dark:text-emerald-400',
        tone === 'bad' && 'text-rose-500',
        tone === 'neutral' && 'text-foreground',
      )}
      >
        {value}
      </p>
      {caption && <p className="text-[11px] text-muted-foreground mt-0.5">{caption}</p>}
    </div>
  )
}

function ChartLegend({ floor, scenario }: { floor: string | null; scenario: boolean }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <span className="h-0.5 w-3 rounded" style={{ background: BALANCE }} />
        {t('spendingPlan.balance')}
      </span>
      {scenario && (
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0 w-3 border-t-2 border-dashed" style={{ borderColor: SCENARIO }} />
          {t('spendingPlan.withPurchase')}
        </span>
      )}
      {floor && (
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0 w-3 border-t border-dashed" style={{ borderColor: FLOOR }} />
          {t('spendingPlan.balanceFloor')} · {floor}
        </span>
      )}
    </div>
  )
}

function PlanChart({
  timeline,
  scenario,
  showFloor,
  currency,
  locale,
  dateLocale,
  money,
}: {
  timeline: SpendingPlanTimeline
  scenario?: SpendingPlanTimeline
  showFloor: boolean
  currency: string
  locale: string
  dateLocale: string
  money: (value: number) => string
}) {
  const { t } = useTranslation()
  const { privacyMode } = usePrivacyMode()
  const scenarioByDate = new Map(scenario?.points.map((point) => [point.date, point.balance]) ?? [])
  const data = timeline.points.map((point) => ({ ...point, scenario: scenarioByDate.get(point.date) }))
  const coveredUntil = timeline.covered_until && !timeline.covered_through_horizon ? timeline.covered_until : null
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="planBalanceFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={BALANCE} stopOpacity={0.18} />
            <stop offset="95%" stopColor={BALANCE} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="date"
          tickFormatter={(value: string) => new Date(`${value}T00:00:00`).toLocaleDateString(dateLocale, { month: 'short', year: '2-digit' })}
          tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
          axisLine={false}
          tickLine={false}
          interval="preserveStartEnd"
          minTickGap={48}
        />
        <YAxis
          tickFormatter={(value: number) => {
            if (privacyMode) return ''
            if (value === 0) return '0'
            return formatCompactCurrency(value, currency, locale)
          }}
          tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
          axisLine={false}
          tickLine={false}
          width={64}
          tickCount={5}
        />
        <Tooltip
          formatter={(value, name) => [money(Number(value)), name]}
          labelFormatter={(label) => formatPlanDate(String(label), dateLocale)}
          contentStyle={TOOLTIP_STYLE}
        />
        {showFloor && <ReferenceLine y={timeline.balance_floor} stroke={FLOOR} strokeDasharray="4 4" strokeOpacity={0.7} />}
        {coveredUntil && (
          <ReferenceLine x={coveredUntil} stroke={FLOOR} strokeOpacity={0.5} />
        )}
        <Area type="monotone" dataKey="balance" stroke="none" tooltipType="none" fill="url(#planBalanceFill)" isAnimationActive={false} />
        <Line
          type="monotone"
          dataKey="balance"
          name={t('spendingPlan.balance')}
          stroke={BALANCE}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 3, fill: BALANCE }}
          isAnimationActive={false}
        />
        {scenario && (
          <Line
            type="monotone"
            dataKey="scenario"
            name={t('spendingPlan.withPurchase')}
            stroke={SCENARIO}
            strokeDasharray="5 4"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3, fill: SCENARIO }}
            isAnimationActive={false}
          />
        )}
      </AreaChart>
    </ResponsiveContainer>
  )
}

function PurchaseSimulator({
  timeline,
  scenario,
  simulated,
  onSimulate,
  savingsTarget,
  money,
  dateLocale,
}: {
  timeline: SpendingPlanTimeline
  scenario?: SpendingPlanTimeline
  simulated: SimulatedPurchase | null
  onSimulate: (purchase: SimulatedPurchase | null) => void
  savingsTarget: string
  money: (value: number) => string
  dateLocale: string
}) {
  const { t } = useTranslation()
  const today = timeline.points[0]?.date ?? ''
  const [form, setForm] = useState({ description: '', amount: '', installments: '1', firstDate: '' })
  const amount = parseAmount(form.amount)

  return (
    <SectionCard>
      <SectionHeader
        title={t('spendingPlan.simulateTitle')}
        subtitle={t('spendingPlan.simulateHint')}
        action={simulated ? (
          <Button size="sm" variant="outline" className="gap-1.5 h-8" onClick={() => onSimulate(null)}>
            <X size={13} /> {t('spendingPlan.clearSimulation')}
          </Button>
        ) : undefined}
      />
      <form
        className="px-4 sm:px-5 py-4 space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (amount <= 0) return
          onSimulate({
            description: form.description || undefined,
            amount,
            installments: Math.min(120, Math.max(1, Math.round(Number(form.installments) || 1))),
            first_date: form.firstDate || today,
          })
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="sim-description">{t('spendingPlan.purchaseDescription')}</Label>
            <Input
              id="sim-description"
              value={form.description}
              placeholder={t('spendingPlan.purchaseDescriptionPlaceholder')}
              onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="sim-amount">{t('spendingPlan.purchaseAmount')}</Label>
            <Input
              id="sim-amount"
              type="number"
              step="0.01"
              min="0"
              placeholder="0,00"
              value={form.amount}
              onChange={(event) => setForm((current) => ({ ...current, amount: event.target.value }))}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="sim-installments">{t('spendingPlan.purchaseInstallments')}</Label>
            <Input
              id="sim-installments"
              type="number"
              min={1}
              max={120}
              value={form.installments}
              onChange={(event) => setForm((current) => ({ ...current, installments: event.target.value }))}
            />
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label>{t('spendingPlan.purchaseFirstDate')}</Label>
            <DatePickerInput
              value={form.firstDate || today}
              onChange={(value) => setForm((current) => ({ ...current, firstDate: value }))}
              className="w-full justify-start"
            />
          </div>
        </div>
        <div className="flex justify-end">
          <Button type="submit" size="sm" className="gap-1.5 h-8" disabled={amount <= 0}>
            <Calculator size={13} /> {t('spendingPlan.simulate')}
          </Button>
        </div>
      </form>
      {scenario && (
        <ScenarioComparison
          without={timeline}
          withPurchase={scenario}
          savingsTarget={savingsTarget}
          money={money}
          dateLocale={dateLocale}
        />
      )}
    </SectionCard>
  )
}

function ScenarioComparison({
  without,
  withPurchase,
  savingsTarget,
  money,
  dateLocale,
}: {
  without: SpendingPlanTimeline
  withPurchase: SpendingPlanTimeline
  savingsTarget: string
  money: (value: number) => string
  dateLocale: string
}) {
  const { t } = useTranslation()
  const coverage = (timeline: SpendingPlanTimeline) => timeline.covered_until
    ? formatPlanDate(timeline.covered_until, dateLocale)
    : t('spendingPlan.notCovered')
  const amountOr = (value: number | null) => (value === null ? '—' : money(value))
  const rows: { label: string; before: string; after: string; worse: boolean }[] = [
    {
      label: t('spendingPlan.coverageLabel'),
      before: coverage(without),
      after: coverage(withPurchase),
      worse: (withPurchase.covered_until ?? '') < (without.covered_until ?? ''),
    },
    {
      label: t('spendingPlan.safeLabel'),
      before: money(without.safe_daily_allowance),
      after: money(withPurchase.safe_daily_allowance),
      worse: withPurchase.safe_daily_allowance < without.safe_daily_allowance,
    },
    {
      label: t('spendingPlan.lowestBalance'),
      before: amountOr(lowestBalance(without.points)),
      after: amountOr(lowestBalance(withPurchase.points)),
      worse: (lowestBalance(withPurchase.points) ?? 0) < (lowestBalance(without.points) ?? 0),
    },
    {
      label: t('spendingPlan.savingsBalance', { date: formatPlanDate(savingsTarget, dateLocale) }),
      before: amountOr(balanceOn(without.points, savingsTarget)),
      after: amountOr(balanceOn(withPurchase.points, savingsTarget)),
      worse: (balanceOn(withPurchase.points, savingsTarget) ?? 0) < (balanceOn(without.points, savingsTarget) ?? 0),
    },
  ]
  return (
    <table className="w-full border-t border-border">
      <thead>
        <tr className="border-b border-border">
          <th className={cn(TH, 'text-left px-4 sm:px-5')}><span className="sr-only">{t('spendingPlan.simulateTitle')}</span></th>
          <th className={cn(TH, 'text-right px-3')}>{t('spendingPlan.withoutPurchase')}</th>
          <th className={cn(TH, 'text-right px-4 sm:px-5')}>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-0 w-3 border-t-2 border-dashed" style={{ borderColor: SCENARIO }} />
              {t('spendingPlan.withPurchase')}
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label} className="border-b border-border last:border-0">
            <td className="py-3 px-4 sm:px-5 text-sm text-muted-foreground">{row.label}</td>
            <td className="py-3 px-3 text-sm text-right tabular-nums text-foreground">{row.before}</td>
            <td className={cn(
              'py-3 px-4 sm:px-5 text-sm text-right tabular-nums font-semibold',
              row.worse ? 'text-rose-500' : 'text-foreground',
            )}
            >
              {row.after}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function SavingsFigure({
  label,
  timeline,
  target,
  money,
  tone = 'default',
}: {
  label: string | null
  timeline: SpendingPlanTimeline
  target: string
  money: (value: number) => string
  tone?: 'default' | 'scenario'
}) {
  const { t } = useTranslation()
  const start = timeline.points[0]?.balance ?? 0
  const end = balanceOn(timeline.points, target)
  if (end === null) return null
  const delta = end - start
  return (
    <div className="rounded-lg border border-border px-4 py-3">
      {label && (
        <p className="text-xs font-medium text-muted-foreground mb-1 inline-flex items-center gap-1.5">
          {tone === 'scenario' && <span className="h-0 w-3 border-t-2 border-dashed" style={{ borderColor: SCENARIO }} />}
          {label}
        </p>
      )}
      <p className="text-xl font-bold tabular-nums text-foreground">{money(end)}</p>
      <p className={cn('text-xs tabular-nums mt-0.5', delta >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-500')}>
        {t('spendingPlan.savingsDelta', { amount: `${delta >= 0 ? '+' : '−'}${money(Math.abs(delta))}` })}
      </p>
    </div>
  )
}

function draftFrom(plan: SpendingPlan | null): SpendingPlanInput {
  return {
    daily_allowance: plan ? Number(plan.daily_allowance) : 0,
    balance_floor: plan ? Number(plan.balance_floor) : 0,
    account_ids: plan?.account_ids ?? null,
    horizon_months: plan?.horizon_months ?? 12,
  }
}

function PlanEditorDialog({ plan, onClose }: { plan: SpendingPlan | null; onClose: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })
  const [draft, setDraft] = useState<SpendingPlanInput>(() => draftFrom(plan))
  const [allowanceText, setAllowanceText] = useState(() => (plan ? String(Number(plan.daily_allowance)) : ''))
  const [floorText, setFloorText] = useState(() => (plan && Number(plan.balance_floor) ? String(Number(plan.balance_floor)) : ''))

  // Previews the amount being typed. Floor, horizon and accounts apply on save.
  const previewAllowance = useDeferredValue(draft.daily_allowance)
  const { data: preview } = useQuery({
    queryKey: ['dashboard', 'spending-plan', 'timeline', previewAllowance],
    queryFn: () => spendingPlanApi.timeline(previewAllowance),
    placeholderData: (previous) => previous,
  })
  const money = (value: number) => mask(formatCurrency(value, preview?.currency ?? 'BRL', locale))

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['spending-plan'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard', 'spending-plan'] })
    queryClient.invalidateQueries({ queryKey: ['transactions', 'calendar'] })
  }
  const save = useMutation({
    mutationFn: () => spendingPlanApi.save(draft),
    onSuccess: () => {
      invalidate()
      toast.success(t('spendingPlan.saved'))
      onClose()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
  const remove = useMutation({
    mutationFn: spendingPlanApi.remove,
    onSuccess: () => {
      invalidate()
      toast.success(t('spendingPlan.removed'))
      onClose()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const planAccounts = useMemo(
    () => sortAccountsByDisplayName((accountsList ?? []).filter((a) => !a.is_closed && a.type !== 'credit_card')),
    [accountsList],
  )
  const isChosen = (id: string, type: string) => (draft.account_ids === null ? type !== 'loan' : draft.account_ids.includes(id))
  const toggleAccount = (id: string) => setDraft((current) => {
    const base = current.account_ids ?? planAccounts.filter((a) => a.type !== 'loan').map((a) => a.id)
    return { ...current, account_ids: base.includes(id) ? base.filter((x) => x !== id) : [...base, id] }
  })

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{plan ? t('spendingPlan.editPlan') : t('spendingPlan.createPlan')}</DialogTitle>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="plan-allowance">{t('spendingPlan.dailyAllowance')}</Label>
            <Input
              id="plan-allowance"
              type="number"
              step="0.01"
              min="0"
              value={allowanceText}
              placeholder="0,00"
              onChange={(event) => {
                const value = event.target.value
                setAllowanceText(value)
                setDraft((current) => ({ ...current, daily_allowance: parseAmount(value) }))
              }}
              required
            />
            {preview && (
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs">
                <span className={preview.covered_until === null ? 'text-rose-500' : 'text-muted-foreground'}>
                  {coverageLabel(preview, dateLocale, t)}
                </span>
                {preview.safe_daily_allowance !== draft.daily_allowance && (
                  <button
                    type="button"
                    className="text-primary hover:underline"
                    onClick={() => {
                      const safe = preview.safe_daily_allowance
                      setAllowanceText(String(safe))
                      setDraft((current) => ({ ...current, daily_allowance: safe }))
                    }}
                  >
                    {t('spendingPlan.useSafe', { amount: money(preview.safe_daily_allowance) })}
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="plan-floor">{t('spendingPlan.balanceFloor')}</Label>
              <Input
                id="plan-floor"
                type="number"
                step="0.01"
                min="0"
                value={floorText}
                placeholder="0,00"
                onChange={(event) => {
                  const value = event.target.value
                  setFloorText(value)
                  setDraft((current) => ({ ...current, balance_floor: parseAmount(value) }))
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="plan-horizon">{t('spendingPlan.horizon')}</Label>
              <select
                id="plan-horizon"
                className={SELECT_CLASS}
                value={draft.horizon_months}
                onChange={(event) => {
                  const months = Number(event.target.value)
                  setDraft((current) => ({ ...current, horizon_months: months }))
                }}
              >
                {HORIZONS.map((months) => (
                  <option key={months} value={months}>{t('spendingPlan.months', { count: months })}</option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-xs text-muted-foreground -mt-2">{t('spendingPlan.balanceFloorHint')}</p>

          <div className="space-y-2">
            <Label>{t('spendingPlan.accounts')}</Label>
            <p className="text-xs text-muted-foreground">{t('spendingPlan.accountsHint')}</p>
            <div className="rounded-lg border border-border divide-y divide-border max-h-48 overflow-y-auto">
              {planAccounts.map((account) => (
                <label key={account.id} className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-muted/40">
                  <input
                    type="checkbox"
                    className="rounded border-border"
                    checked={isChosen(account.id, account.type)}
                    onChange={() => toggleAccount(account.id)}
                  />
                  <AccountIcon account={account} size="sm" />
                  <span className="text-sm text-foreground truncate">{getAccountName(account)}</span>
                </label>
              ))}
            </div>
          </div>

          <DialogFooter className="sm:justify-between gap-2">
            {plan ? (
              <Button
                type="button"
                variant="ghost"
                className="text-rose-500 hover:text-rose-600"
                onClick={() => remove.mutate()}
                disabled={remove.isPending}
              >
                {t('spendingPlan.remove')}
              </Button>
            ) : <span />}
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
              <Button type="submit" disabled={save.isPending}>{t('common.save')}</Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
