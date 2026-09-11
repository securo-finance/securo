import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Landmark } from 'lucide-react'
import { toast } from 'sonner'
import { accounts, currencies } from '@/lib/api'
import { localDateString } from '@/lib/date-utils'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { formatCurrency } from '@/lib/format'
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
import { PageHeader } from '@/components/page-header'
import { AccountIcon } from '@/components/account-icon'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import type { Account } from '@/types'

const LOAN_KINDS = ['home', 'personal', 'auto', 'education', 'gold', 'other'] as const

function outstandingOf(acc: Account): number {
  return Math.abs(Number(acc.current_balance ?? acc.balance ?? 0))
}

export default function LoansPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()

  const { data: accountsList, isLoading } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accounts.list(false),
  })
  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currencies.list,
    staleTime: Infinity,
  })

  const loans = useMemo(
    () => (accountsList ?? []).filter((a) => a.type === 'loan' && !a.is_closed),
    [accountsList],
  )

  const totals = useMemo(() => {
    const outstanding = loans.reduce((s, a) => s + outstandingOf(a), 0)
    const emi = loans.reduce((s, a) => s + Number(a.emi_amount || 0), 0)
    return { outstanding, emi, count: loans.length }
  }, [loans])

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Account | null>(null)
  const [name, setName] = useState('')
  const [kind, setKind] = useState<string>('home')
  const [currency, setCurrency] = useState(userCurrency)
  const [outstanding, setOutstanding] = useState('')
  const [principal, setPrincipal] = useState('')
  const [rate, setRate] = useState('')
  const [tenure, setTenure] = useState('')
  const [emi, setEmi] = useState('')
  const [emiDay, setEmiDay] = useState('5')
  const [disbursedOn, setDisbursedOn] = useState(localDateString(new Date()))

  function openCreate() {
    setEditing(null)
    setName('')
    setKind('home')
    setCurrency(userCurrency)
    setOutstanding('')
    setPrincipal('')
    setRate('')
    setTenure('')
    setEmi('')
    setEmiDay('5')
    setDisbursedOn(localDateString(new Date()))
    setDialogOpen(true)
  }

  function openEdit(acc: Account) {
    setEditing(acc)
    setName(acc.display_name || acc.name)
    setKind(acc.loan_kind || 'other')
    setCurrency(acc.currency)
    setOutstanding(String(outstandingOf(acc)))
    setPrincipal(acc.original_principal != null ? String(acc.original_principal) : '')
    setRate(acc.interest_rate != null ? String(acc.interest_rate) : '')
    setTenure(acc.tenure_months != null ? String(acc.tenure_months) : '')
    setEmi(acc.emi_amount != null ? String(acc.emi_amount) : '')
    setEmiDay(acc.emi_day != null ? String(acc.emi_day) : '5')
    setDisbursedOn(acc.disbursed_on ?? '')
    setDialogOpen(true)
  }

  function payload() {
    const bal = outstanding ? parseFloat(outstanding) : 0
    return {
      name,
      type: 'loan' as const,
      balance: bal,
      currency,
      loan_kind: kind,
      original_principal: principal ? parseFloat(principal) : bal || null,
      interest_rate: rate ? parseFloat(rate) : null,
      tenure_months: tenure ? parseInt(tenure, 10) : null,
      emi_amount: emi ? parseFloat(emi) : null,
      disbursed_on: disbursedOn || null,
      emi_day: emiDay ? parseInt(emiDay, 10) : null,
    }
  }

  const createMutation = useMutation({
    mutationFn: () => accounts.create(payload()),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      toast.success(t('loans.created'))
      setDialogOpen(false)
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: () => accounts.update(editing!.id, payload()),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      toast.success(t('loans.updated'))
      setDialogOpen(false)
    },
    onError: () => toast.error(t('common.error')),
  })

  const saving = createMutation.isPending || updateMutation.isPending

  return (
    <div className="space-y-6">
      <PageHeader
        section={t('loans.section')}
        title={t('loans.title')}
        action={
          canWrite ? (
            <Button onClick={openCreate} className="gap-1.5">
              <Plus size={16} />
              {t('loans.add')}
            </Button>
          ) : undefined
        }
      />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <SummaryCard label={t('loans.totalOutstanding')} value={mask(formatCurrency(totals.outstanding, userCurrency, locale))} />
        <SummaryCard label={t('loans.monthlyEmi')} value={mask(formatCurrency(totals.emi, userCurrency, locale))} />
        <SummaryCard label={t('loans.count')} value={String(totals.count)} />
      </div>

      {isLoading ? (
        <div className="h-32 rounded-xl border border-border bg-muted/30 animate-pulse" />
      ) : loans.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border p-10 text-center">
          <Landmark className="mx-auto mb-3 text-muted-foreground" size={28} />
          <p className="font-medium">{t('loans.empty')}</p>
          <p className="text-sm text-muted-foreground mt-1">{t('loans.emptyHint')}</p>
        </div>
      ) : (
        <div className="rounded-xl border border-border divide-y divide-border overflow-hidden">
          {loans.map((acc) => {
            const owed = outstandingOf(acc)
            const orig = Number(acc.original_principal || 0)
            const pct = orig > 0 ? Math.min(100, Math.round(((orig - owed) / orig) * 100)) : null
            return (
              <button
                key={acc.id}
                type="button"
                onClick={() => navigate(`/loans/${acc.id}`)}
                className="w-full text-left px-4 py-3 hover:bg-muted/30 flex items-center gap-3"
              >
                <AccountIcon account={acc} size="md" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold truncate">{acc.display_name || acc.name}</span>
                    <span className="text-[11px] text-muted-foreground shrink-0">
                      {t(`loans.kind.${acc.loan_kind || 'other'}`)}
                    </span>
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5 flex flex-wrap gap-x-3">
                    {acc.interest_rate != null && <span>{acc.interest_rate}% p.a.</span>}
                    {acc.emi_amount != null && (
                      <span>{t('loans.emi')} {mask(formatCurrency(acc.emi_amount, acc.currency, locale))}</span>
                    )}
                    {acc.tenure_months != null && <span>{acc.tenure_months} {t('loans.months')}</span>}
                  </div>
                  {pct != null && (
                    <div className="mt-2 h-1.5 rounded-full bg-muted overflow-hidden">
                      <div className="h-full bg-emerald-500" style={{ width: `${pct}%` }} />
                    </div>
                  )}
                </div>
                <div className="text-right shrink-0">
                  <p className="font-semibold tabular-nums text-rose-500">
                    {mask(formatCurrency(owed, acc.currency, locale))}
                  </p>
                  <button
                    type="button"
                    className="text-[11px] text-primary mt-1"
                    onClick={(e) => {
                      e.stopPropagation()
                      openEdit(acc)
                    }}
                  >
                    {t('common.edit')}
                  </button>
                </div>
              </button>
            )
          })}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? t('loans.edit') : t('loans.add')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>{t('loans.productName')}</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('loans.namePlaceholder')} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>{t('loans.kindLabel')}</Label>
                <select
                  className="bg-card border border-border rounded-lg px-3 py-2 text-sm w-full"
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
                >
                  {LOAN_KINDS.map((k) => (
                    <option key={k} value={k}>{t(`loans.kind.${k}`)}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label>{t('accounts.currency')}</Label>
                <select
                  className="bg-card border border-border rounded-lg px-3 py-2 text-sm w-full"
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                  disabled={!!editing}
                >
                  {(supportedCurrencies ?? [{ code: userCurrency, name: userCurrency, flag: '' }]).map((c) => (
                    <option key={c.code} value={c.code}>{c.flag} {c.code}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>{t('loans.outstanding')}</Label>
                <Input type="number" inputMode="decimal" value={outstanding} onChange={(e) => setOutstanding(e.target.value)} />
                <p className="text-[11px] text-muted-foreground">{t('loans.outstandingHint')}</p>
              </div>
              <div className="space-y-1.5">
                <Label>{t('loans.originalPrincipal')}</Label>
                <Input type="number" inputMode="decimal" value={principal} onChange={(e) => setPrincipal(e.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>{t('loans.interestRate')}</Label>
                <Input type="number" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>{t('loans.tenureMonths')}</Label>
                <Input type="number" inputMode="numeric" value={tenure} onChange={(e) => setTenure(e.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>{t('loans.emi')}</Label>
                <Input type="number" inputMode="decimal" value={emi} onChange={(e) => setEmi(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>{t('loans.emiDay')}</Label>
                <Input type="number" min={1} max={28} value={emiDay} onChange={(e) => setEmiDay(e.target.value)} />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>{t('loans.disbursedOn')}</Label>
              <DatePickerInput value={disbursedOn} onChange={setDisbursedOn} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>{t('common.cancel')}</Button>
            <Button
              disabled={!name.trim() || saving}
              onClick={() => (editing ? updateMutation.mutate() : createMutation.mutate())}
            >
              {t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums mt-1">{value}</p>
    </div>
  )
}
