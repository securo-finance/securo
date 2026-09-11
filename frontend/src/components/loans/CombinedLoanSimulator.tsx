import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Calculator, GitCommitHorizontal, Plus, Trash2 } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { formatCurrency } from '@/lib/format'
import { localDateString } from '@/lib/date-utils'
import { accounts as accountsApi } from '@/lib/api'

type EventType = 'one_time_prepayment' | 'recurring_prepayment' | 'rate_change' | 'emi_holiday'

interface SimEvent {
  id: string
  type: EventType
  date: string
  amount?: string
  new_rate?: string
  months?: string
  end_date?: string
  label?: string
}

interface CombinedResult {
  kpis: {
    total_interest: number
    baseline_interest: number
    interest_saved: number
    months_saved: number
    emi: number
    payoff_date: string | null
    baseline_payoff_date: string | null
    scenario_months: number
    baseline_months: number
  }
  timeline: Array<{
    due_date: string
    emi_number: number
    opening_balance: number
    interest: number
    principal: number
    extra_principal: number
    closing_balance: number
    rate: number
    events: string[]
    holiday: boolean
  }>
  curves: {
    dates: string[]
    outstanding: number[]
    cumulative_interest: number[]
    cumulative_principal: number[]
  }
  baseline_curves: {
    dates: string[]
    outstanding: number[]
  }
}

interface Commitment {
  id: string
  kind: string
  amount: number
  start_date: string
  end_date?: string | null
  status: string
  budget_id?: string | null
  recurring_transaction_id?: string | null
  notes?: string | null
}

function authHeaders(): HeadersInit {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${localStorage.getItem('token')}`,
    'X-Workspace-Id': localStorage.getItem('workspace_id') || '',
  }
}

function newEvent(partial?: Partial<SimEvent>): SimEvent {
  return {
    id: crypto.randomUUID(),
    type: 'recurring_prepayment',
    date: localDateString(new Date()),
    amount: '200',
    months: '24',
    ...partial,
  }
}

function Sparkline({
  values,
  baseline,
  color = '#10b981',
}: {
  values: number[]
  baseline?: number[]
  color?: string
}) {
  const w = 320
  const h = 80
  const all = [...values, ...(baseline || [])]
  if (all.length < 2) return null
  const min = Math.min(...all)
  const max = Math.max(...all)
  const span = max - min || 1
  const path = (arr: number[]) =>
    arr
      .map((v, i) => {
        const x = (i / (arr.length - 1)) * w
        const y = h - ((v - min) / span) * (h - 8) - 4
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-20">
      {baseline && baseline.length > 1 && (
        <path d={path(baseline)} fill="none" stroke="#94a3b8" strokeWidth="1.5" strokeDasharray="4 3" />
      )}
      <path d={path(values)} fill="none" stroke={color} strokeWidth="2" />
    </svg>
  )
}

function StackedArea({
  dates,
  principal,
  interest,
}: {
  dates: string[]
  principal: number[]
  interest: number[]
}) {
  const w = 320
  const h = 90
  const n = Math.min(dates.length, principal.length, interest.length)
  if (n < 2) return null
  const totals = Array.from({ length: n }, (_, i) => principal[i] + interest[i])
  const max = Math.max(...totals) || 1
  const xAt = (i: number) => (i / (n - 1)) * w
  const yAt = (v: number) => h - (v / max) * (h - 6) - 3
  const top = Array.from({ length: n }, (_, i) => `${xAt(i).toFixed(1)},${yAt(totals[i]).toFixed(1)}`).join(' ')
  const mid = Array.from({ length: n }, (_, i) => `${xAt(i).toFixed(1)},${yAt(principal[i]).toFixed(1)}`).join(' ')
  const bottomRight = `${xAt(n - 1).toFixed(1)},${h}`
  const bottomLeft = `0,${h}`
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-24">
      <polygon points={`${top} ${bottomRight} ${bottomLeft}`} fill="#f59e0b33" stroke="#f59e0b" strokeWidth="1" />
      <polygon points={`${mid} ${bottomRight} ${bottomLeft}`} fill="#10b98155" stroke="#10b981" strokeWidth="1" />
    </svg>
  )
}

interface Props {
  accountId: string
  currentRate?: number
}

export function CombinedLoanSimulator({ accountId, currentRate = 0 }: Props) {
  const qc = useQueryClient()
  const [events, setEvents] = useState<SimEvent[]>([
    newEvent({ type: 'one_time_prepayment', amount: '5000', label: 'Bonus prepay' }),
    newEvent({ type: 'recurring_prepayment', amount: '200', months: '36', label: '+$200/mo' }),
    newEvent({
      type: 'rate_change',
      date: localDateString(new Date(Date.now() + 1000 * 60 * 60 * 24 * 180)),
      new_rate: currentRate ? String(Math.max(currentRate - 0.5, 0.1)) : '5.5',
      label: 'Rate cut',
      amount: undefined,
      months: undefined,
    }),
  ])
  const [strategy, setStrategy] = useState<'reduce_tenure' | 'reduce_emi'>('reduce_tenure')
  const [result, setResult] = useState<CombinedResult | null>(null)
  const [error, setError] = useState('')
  const [commitKind, setCommitKind] = useState<'one_time_prepayment' | 'recurring_prepayment'>(
    'recurring_prepayment',
  )
  const [commitAmount, setCommitAmount] = useState('200')
  const [commitStart, setCommitStart] = useState(localDateString(new Date()))

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(false),
  })
  const checking = useMemo(
    () => (accountsList ?? []).find((a) => a.type === 'checking' && !a.is_closed),
    [accountsList],
  )

  const { data: commitments = [], refetch: refetchCommitments } = useQuery({
    queryKey: ['loan-commitments', accountId],
    queryFn: async () => {
      const r = await fetch(`/api/v1/loans/commitments?account_id=${accountId}`, {
        headers: authHeaders(),
      })
      if (!r.ok) throw new Error('Failed to load commitments')
      return (await r.json()) as Commitment[]
    },
  })

  const runSim = useMutation({
    mutationFn: async () => {
      const payload = {
        account_id: accountId,
        strategy,
        events: events.map((e) => {
          const base: Record<string, unknown> = { type: e.type, date: e.date, label: e.label }
          if (e.type === 'one_time_prepayment' || e.type === 'recurring_prepayment') {
            base.amount = parseFloat(e.amount || '0')
          }
          if (e.type === 'recurring_prepayment' || e.type === 'emi_holiday') {
            if (e.months) base.months = parseInt(e.months, 10)
            if (e.end_date) base.end_date = e.end_date
          }
          if (e.type === 'rate_change') base.new_rate = parseFloat(e.new_rate || '0')
          return base
        }),
      }
      const r = await fetch('/api/v1/loans/simulations/combined', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify(payload),
      })
      if (!r.ok) {
        const detail = await r.text()
        throw new Error(detail || 'Simulation failed')
      }
      return (await r.json()) as CombinedResult
    },
    onSuccess: (data) => {
      setResult(data)
      setError('')
    },
    onError: (err: Error) => setError(err.message || 'Simulation failed'),
  })

  const commitMutation = useMutation({
    mutationFn: async () => {
      // Resolve Housing category via categories list if present on window-less fetch
      const catsRes = await fetch('/api/categories', { headers: authHeaders() })
      const cats = catsRes.ok ? await catsRes.json() : []
      const housing = (cats as Array<{ id: string; name: string }>).find((c) => c.name === 'Housing')
      const body = {
        account_id: accountId,
        kind: commitKind,
        amount: parseFloat(commitAmount),
        start_date: commitStart,
        day_of_month: parseInt(commitStart.slice(8, 10), 10) || 1,
        funding_account_id: checking?.id,
        category_id: housing?.id,
        notes: 'Committed from Combined Loan Simulator',
        ...(commitKind === 'recurring_prepayment' ? { end_date: null } : {}),
      }
      const r = await fetch('/api/v1/loans/commitments', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error(await r.text())
      return r.json()
    },
    onSuccess: () => {
      toast.success('Plan committed — budget + forecast updated')
      refetchCommitments()
      qc.invalidateQueries({ queryKey: ['budgets'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
      qc.invalidateQueries({ queryKey: ['projected-transactions'] })
      qc.invalidateQueries({ queryKey: ['recurring'] })
    },
    onError: () => toast.error('Failed to commit plan'),
  })

  const cancelMutation = useMutation({
    mutationFn: async (id: string) => {
      const r = await fetch(`/api/v1/loans/commitments/${id}/cancel`, {
        method: 'POST',
        headers: authHeaders(),
      })
      if (!r.ok) throw new Error('Cancel failed')
      return r.json()
    },
    onSuccess: () => {
      toast.success('Commitment cancelled')
      refetchCommitments()
      qc.invalidateQueries({ queryKey: ['budgets'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })

  const updateEvent = (id: string, patch: Partial<SimEvent>) => {
    setEvents((prev) => prev.map((e) => (e.id === id ? { ...e, ...patch } : e)))
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-lg">
            <Calculator className="h-5 w-5" />
            Combined scenario ground
          </CardTitle>
          <div className="flex items-center gap-2">
            <select
              className="bg-card border border-border rounded-lg px-3 py-2 text-sm"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as 'reduce_tenure' | 'reduce_emi')}
            >
              <option value="reduce_tenure">Strategy: reduce tenure</option>
              <option value="reduce_emi">Strategy: reduce EMI</option>
            </select>
            <Button onClick={() => runSim.mutate()} disabled={runSim.isPending}>
              {runSim.isPending ? 'Running…' : 'Run combined sim'}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Stack one-time prepays, recurring extras, rate changes, and EMI holidays on one timeline.
            KPIs compare against a no-event baseline.
          </p>

          <div className="space-y-3">
            {events.map((ev) => (
              <div key={ev.id} className="grid grid-cols-1 md:grid-cols-6 gap-2 items-end border rounded-lg p-3">
                <div className="space-y-1">
                  <Label>Type</Label>
                  <select
                    className="bg-card border border-border rounded-lg px-2 py-2 text-sm w-full"
                    value={ev.type}
                    onChange={(e) => updateEvent(ev.id, { type: e.target.value as EventType })}
                  >
                    <option value="one_time_prepayment">One-time prepay</option>
                    <option value="recurring_prepayment">Recurring prepay</option>
                    <option value="rate_change">Rate change</option>
                    <option value="emi_holiday">EMI holiday</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <Label>Date</Label>
                  <Input type="date" value={ev.date} onChange={(e) => updateEvent(ev.id, { date: e.target.value })} />
                </div>
                {(ev.type === 'one_time_prepayment' || ev.type === 'recurring_prepayment') && (
                  <div className="space-y-1">
                    <Label>Amount</Label>
                    <Input
                      type="number"
                      value={ev.amount || ''}
                      onChange={(e) => updateEvent(ev.id, { amount: e.target.value })}
                    />
                  </div>
                )}
                {ev.type === 'rate_change' && (
                  <div className="space-y-1">
                    <Label>New rate %</Label>
                    <Input
                      type="number"
                      value={ev.new_rate || ''}
                      onChange={(e) => updateEvent(ev.id, { new_rate: e.target.value })}
                    />
                  </div>
                )}
                {(ev.type === 'recurring_prepayment' || ev.type === 'emi_holiday') && (
                  <div className="space-y-1">
                    <Label>Months</Label>
                    <Input
                      type="number"
                      value={ev.months || ''}
                      onChange={(e) => updateEvent(ev.id, { months: e.target.value })}
                    />
                  </div>
                )}
                <div className="space-y-1">
                  <Label>Label</Label>
                  <Input value={ev.label || ''} onChange={(e) => updateEvent(ev.id, { label: e.target.value })} />
                </div>
                <Button variant="ghost" size="icon" onClick={() => setEvents((p) => p.filter((x) => x.id !== ev.id))}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>

          <Button variant="outline" onClick={() => setEvents((p) => [...p, newEvent()])}>
            <Plus className="h-4 w-4 mr-2" />
            Add event
          </Button>

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {result && (
            <div className="space-y-4 pt-2">
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <Kpi label="Interest saved" value={formatCurrency(result.kpis.interest_saved)} accent />
                <Kpi label="Months saved" value={String(result.kpis.months_saved)} />
                <Kpi label="EMI" value={formatCurrency(result.kpis.emi)} />
                <Kpi label="Payoff" value={result.kpis.payoff_date || '—'} />
                <Kpi label="Scenario interest" value={formatCurrency(result.kpis.total_interest)} />
              </div>

              <div className="grid md:grid-cols-2 gap-4">
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Outstanding principal</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <Sparkline values={result.curves.outstanding} baseline={result.baseline_curves.outstanding} />
                    <p className="text-[11px] text-muted-foreground mt-1">Solid = scenario · dashed = baseline</p>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Cumulative principal vs interest</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <StackedArea
                      dates={result.curves.dates}
                      principal={result.curves.cumulative_principal}
                      interest={result.curves.cumulative_interest}
                    />
                    <p className="text-[11px] text-muted-foreground mt-1">Green principal · amber interest</p>
                  </CardContent>
                </Card>
              </div>

              <div className="overflow-auto max-h-64 border rounded-lg">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-muted/80">
                    <tr className="text-left">
                      <th className="p-2">#</th>
                      <th className="p-2">Due</th>
                      <th className="p-2">Interest</th>
                      <th className="p-2">Principal</th>
                      <th className="p-2">Extra</th>
                      <th className="p-2">Closing</th>
                      <th className="p-2">Events</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.timeline.slice(0, 60).map((row) => (
                      <tr key={row.emi_number} className="border-t">
                        <td className="p-2">{row.emi_number}</td>
                        <td className="p-2">{row.due_date}</td>
                        <td className="p-2">{formatCurrency(row.interest)}</td>
                        <td className="p-2">{formatCurrency(row.principal)}</td>
                        <td className="p-2">{formatCurrency(row.extra_principal)}</td>
                        <td className="p-2">{formatCurrency(row.closing_balance)}</td>
                        <td className="p-2 text-muted-foreground">{row.events.join(', ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <GitCommitHorizontal className="h-5 w-5" />
            Commit to budget + forecast
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Committing creates a budget line and a recurring/pending cash outflow on{' '}
            {checking ? checking.name : 'your checking account'} so dashboard forecast picks it up.
          </p>
          <div className="grid md:grid-cols-4 gap-3 items-end">
            <div className="space-y-1">
              <Label>Kind</Label>
              <select
                className="bg-card border border-border rounded-lg px-3 py-2 text-sm w-full"
                value={commitKind}
                onChange={(e) => setCommitKind(e.target.value as typeof commitKind)}
              >
                <option value="recurring_prepayment">Recurring extra</option>
                <option value="one_time_prepayment">One-time prepay</option>
              </select>
            </div>
            <div className="space-y-1">
              <Label>Amount</Label>
              <Input type="number" value={commitAmount} onChange={(e) => setCommitAmount(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>Start</Label>
              <Input type="date" value={commitStart} onChange={(e) => setCommitStart(e.target.value)} />
            </div>
            <Button onClick={() => commitMutation.mutate()} disabled={commitMutation.isPending || !checking}>
              {commitMutation.isPending ? 'Committing…' : 'Commit plan'}
            </Button>
          </div>

          <div className="space-y-2">
            <h4 className="text-sm font-medium">Active commitments</h4>
            {commitments.filter((c) => c.status === 'active').length === 0 && (
              <p className="text-sm text-muted-foreground">None yet.</p>
            )}
            {commitments
              .filter((c) => c.status === 'active')
              .map((c) => (
                <div key={c.id} className="flex items-center justify-between border rounded-lg px-3 py-2 text-sm">
                  <div>
                    <div className="font-medium">
                      {c.kind === 'recurring_prepayment' ? 'Recurring' : 'One-time'} {formatCurrency(c.amount)}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      from {c.start_date}
                      {c.budget_id ? ' · budget linked' : ''}
                      {c.recurring_transaction_id ? ' · forecast recurring' : ''}
                    </div>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => cancelMutation.mutate(c.id)}>
                    Cancel
                  </Button>
                </div>
              ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function Kpi({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${accent ? 'text-emerald-600' : ''}`}>{value}</div>
    </div>
  )
}
