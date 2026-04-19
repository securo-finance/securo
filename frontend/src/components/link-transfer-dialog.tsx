import { useMemo, useState } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { ArrowRight, AlertTriangle, Info, ArrowLeft, Search, Sparkles } from 'lucide-react'
import { transactions as transactionsApi } from '@/lib/api'
import type { Account, Transaction } from '@/types'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

type CounterpartCardProps = {
  label: string
  description: string
  account: string
  date: string
  amount: number
  currency: string
  sign: '+' | '−'
  locale: string
}

function CounterpartCard({ label, description, account, date, amount, currency, sign, locale }: CounterpartCardProps) {
  const colorClass = sign === '+' ? 'text-emerald-600' : 'text-rose-500'
  return (
    <div className="rounded-lg border border-border bg-muted/30 p-3">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-1">
        {label}
      </p>
      <p className="text-sm font-semibold text-foreground truncate" title={description}>
        {description}
      </p>
      <p className="text-xs text-muted-foreground truncate">{account}</p>
      <p className="text-xs text-muted-foreground mt-1">
        {new Date(date + 'T00:00:00').toLocaleDateString(locale)}
      </p>
      <p className={`text-sm font-bold tabular-nums ${colorClass} mt-2`}>
        {sign}
        {formatCurrency(amount, currency, locale)}
      </p>
    </div>
  )
}

type Props = {
  open: boolean
  onClose: () => void
  /** Direct mode (legacy 2-selection): both must be provided. */
  debit?: Transaction | null
  credit?: Transaction | null
  /** Picker mode: pass a single anchor transaction. */
  anchor?: Transaction | null
  accounts: Account[]
  onConfirm: (debitId: string, creditId: string) => void
  loading: boolean
}

export function LinkTransferDialog({
  open,
  onClose,
  debit,
  credit,
  anchor,
  accounts,
  onConfirm,
  loading,
}: Props) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language

  const isDirectMode = !!(debit && credit)
  const isPickerMode = !!anchor && !isDirectMode

  // In picker mode, the user clicks a candidate to "select" it; that promotes
  // us into a small confirm step which reuses the same FROM/TO card layout.
  const [pickedCandidate, setPickedCandidate] = useState<Transaction | null>(null)
  const [searchTerm, setSearchTerm] = useState('')

  // Reset internal picker state when the dialog opens with a new anchor or
  // when it transitions from open → closed. Done during render via the
  // "adjusting state on prop change" pattern to avoid effect cascades.
  const sessionKey = `${open ? '1' : '0'}-${anchor?.id ?? ''}`
  const [prevSessionKey, setPrevSessionKey] = useState(sessionKey)
  if (sessionKey !== prevSessionKey) {
    setPrevSessionKey(sessionKey)
    setPickedCandidate(null)
    setSearchTerm('')
  }

  const { data: candidates, isLoading: candidatesLoading } = useQuery({
    queryKey: ['transfer-candidates', anchor?.id],
    queryFn: () => transactionsApi.transferCandidates(anchor!.id),
    enabled: open && isPickerMode,
  })

  const filteredCandidates = useMemo(() => {
    if (!candidates) return []
    const term = searchTerm.trim().toLowerCase()
    if (!term) return candidates
    return candidates.filter((c) => c.description.toLowerCase().includes(term))
  }, [candidates, searchTerm])

  // The "Best match" badge should only appear when the top-ranked candidate
  // is genuinely close to the anchor — same date neighborhood AND amount
  // close enough to suggest they're the same transfer (allowing for FX/fees).
  const bestMatchId = useMemo(() => {
    if (!candidates || candidates.length === 0 || !anchor) return null
    const top = candidates[0]
    const anchorDate = new Date(anchor.date + 'T00:00:00').getTime()
    const candidateDate = new Date(top.date + 'T00:00:00').getTime()
    const dayDiff = Math.abs(candidateDate - anchorDate) / (1000 * 60 * 60 * 24)
    if (dayDiff > 3) return null
    const anchorAmount = Math.abs(Number(anchor.amount_primary ?? anchor.amount))
    const topAmount = Math.abs(Number(top.amount_primary ?? top.amount))
    if (anchorAmount === 0) return null
    const pctDiff = Math.abs(anchorAmount - topAmount) / anchorAmount
    if (pctDiff > 0.02) return null
    return top.id
  }, [candidates, anchor])

  // Resolve which transactions are currently shown as the FROM/TO pair.
  const effectiveDebit: Transaction | null = useMemo(() => {
    if (isDirectMode) return debit ?? null
    if (!anchor || !pickedCandidate) return null
    return anchor.type === 'debit' ? anchor : pickedCandidate
  }, [isDirectMode, debit, anchor, pickedCandidate])

  const effectiveCredit: Transaction | null = useMemo(() => {
    if (isDirectMode) return credit ?? null
    if (!anchor || !pickedCandidate) return null
    return anchor.type === 'credit' ? anchor : pickedCandidate
  }, [isDirectMode, credit, anchor, pickedCandidate])

  if (!open) return null
  if (!isDirectMode && !isPickerMode) return null

  // Picker view (no candidate selected yet)
  if (isPickerMode && !pickedCandidate) {
    const anchorAccount = accounts.find((a) => a.id === anchor!.account_id)
    const anchorAmount = Math.abs(Number(anchor!.amount))

    return (
      <Dialog open={open} onOpenChange={onClose}>
        <DialogContent className="sm:max-w-xl overflow-hidden">
          <DialogHeader>
            <DialogTitle>{t('transactions.linkTransferPickerTitle')}</DialogTitle>
          </DialogHeader>

          <div className="space-y-4 min-w-0">
            <p className="text-sm text-muted-foreground">
              {t('transactions.linkTransferPickerDescription')}
            </p>

            <CounterpartCard
              label={t('transactions.linkTransferAnchor')}
              description={anchor!.description}
              account={anchorAccount?.name ?? '—'}
              date={anchor!.date}
              amount={anchorAmount}
              currency={anchor!.currency}
              sign={anchor!.type === 'debit' ? '−' : '+'}
              locale={locale}
            />

            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder={t('transactions.linkTransferSearchPlaceholder')}
                className="w-full pl-9 pr-3 py-2 text-sm rounded-md border border-border bg-card text-foreground focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
              />
            </div>

            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                {t('transactions.linkTransferCandidatesHeader')}
              </p>
              {candidatesLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-14 w-full" />
                  ))}
                </div>
              ) : filteredCandidates.length === 0 ? (
                <p className="text-xs text-muted-foreground italic py-4 text-center">
                  {t('transactions.linkTransferNoCandidates')}
                </p>
              ) : (
                <ul className="max-h-72 overflow-y-auto space-y-1.5 -mx-1 px-1">
                  {filteredCandidates.map((c) => {
                    const account = accounts.find((a) => a.id === c.account_id)
                    const amount = Math.abs(Number(c.amount))
                    const sign = c.type === 'debit' ? '−' : '+'
                    const colorClass = c.type === 'debit' ? 'text-rose-500' : 'text-emerald-600'
                    const isBest = !searchTerm && c.id === bestMatchId
                    return (
                      <li key={c.id}>
                        <button
                          type="button"
                          onClick={() => setPickedCandidate(c)}
                          className="w-full text-left rounded-lg border border-border bg-card hover:bg-muted/50 hover:border-primary/40 transition-colors p-3 flex items-center gap-3 min-w-0"
                        >
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 min-w-0">
                              <p className="text-sm font-semibold text-foreground truncate min-w-0 flex-1">
                                {c.description}
                              </p>
                              {isBest && (
                                <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-primary bg-primary/5 border border-primary/15 px-1.5 py-0.5 rounded-full shrink-0">
                                  <Sparkles size={10} />
                                  {t('transactions.linkTransferBestMatch', 'Best match')}
                                </span>
                              )}
                            </div>
                            <p className="text-xs text-muted-foreground truncate">
                              {account ? getAccountName(account) : '—'} · {new Date(c.date + 'T00:00:00').toLocaleDateString(locale)}
                            </p>
                          </div>
                          <p className={`text-sm font-bold tabular-nums ${colorClass} shrink-0`}>
                            {sign}
                            {formatCurrency(amount, c.currency, locale)}
                          </p>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={loading}>
              {t('common.cancel')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }

  // Confirm view — used in direct mode and after picking a candidate.
  if (!effectiveDebit || !effectiveCredit) return null

  const fromAccount = accounts.find((a) => a.id === effectiveDebit.account_id)
  const toAccount = accounts.find((a) => a.id === effectiveCredit.account_id)
  const sameCurrency = effectiveDebit.currency === effectiveCredit.currency
  const debitAmount = Math.abs(Number(effectiveDebit.amount))
  const creditAmount = Math.abs(Number(effectiveCredit.amount))
  const amountMismatch = sameCurrency && debitAmount !== creditAmount

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('transactions.linkTransferTitle')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {t('transactions.linkTransferDescription')}
          </p>

          <div className="grid grid-cols-[1fr_auto_1fr] items-stretch gap-3">
            <CounterpartCard
              label={t('transactions.linkTransferFrom')}
              description={effectiveDebit.description}
              account={fromAccount?.name ?? '—'}
              date={effectiveDebit.date}
              amount={debitAmount}
              currency={effectiveDebit.currency}
              sign="−"
              locale={locale}
            />
            <div className="flex items-center">
              <ArrowRight size={18} className="text-muted-foreground" />
            </div>
            <CounterpartCard
              label={t('transactions.linkTransferTo')}
              description={effectiveCredit.description}
              account={toAccount?.name ?? '—'}
              date={effectiveCredit.date}
              amount={creditAmount}
              currency={effectiveCredit.currency}
              sign="+"
              locale={locale}
            />
          </div>

          {amountMismatch && (
            <div className="flex items-start gap-2 p-3 text-xs bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 rounded-md text-amber-800 dark:text-amber-300">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>{t('transactions.linkTransferAmountMismatch')}</span>
            </div>
          )}

          <div className="flex items-start gap-2 p-3 text-xs bg-muted/50 border border-border rounded-md text-muted-foreground">
            <Info size={14} className="shrink-0 mt-0.5" />
            <span>{t('transactions.linkTransferCascadeWarning')}</span>
          </div>
        </div>

        <DialogFooter className="gap-2 sm:gap-2">
          {isPickerMode && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => setPickedCandidate(null)}
              disabled={loading}
              className="mr-auto"
            >
              <ArrowLeft size={14} className="mr-1" />
              {t('transactions.linkTransferBack')}
            </Button>
          )}
          <Button type="button" variant="outline" onClick={onClose} disabled={loading}>
            {t('common.cancel')}
          </Button>
          <Button
            type="button"
            onClick={() => onConfirm(effectiveDebit.id, effectiveCredit.id)}
            disabled={loading}
          >
            {loading ? t('common.loading') : t('transactions.linkTransferConfirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
