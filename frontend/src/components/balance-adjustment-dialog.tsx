import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Account } from '@/types'
import { getBalanceAdjustmentPreview } from '@/lib/balance-adjustment'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type BalanceAdjustmentDialogProps = {
  open: boolean
  account: Account
  currentBalance: number
  loading: boolean
  onClose: () => void
  onSave: (input: { balance: number; exclude_from_pnl: boolean }) => void
}

export function BalanceAdjustmentDialog({
  open,
  account,
  currentBalance,
  loading,
  onClose,
  onSave,
}: BalanceAdjustmentDialogProps) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const currentInputValue = account.type === 'credit_card'
    ? Math.max(0, -currentBalance)
    : currentBalance
  const [target, setTarget] = useState(currentInputValue.toFixed(2))
  const [excludeFromReports, setExcludeFromReports] = useState(true)

  const numericTarget = Number(target)
  const validTarget = Number.isFinite(numericTarget)
    && (account.type !== 'credit_card' || numericTarget >= 0)
  const preview = useMemo(
    () => validTarget
      ? getBalanceAdjustmentPreview(account.type, currentBalance, numericTarget)
      : null,
    [account.type, currentBalance, numericTarget, validTarget],
  )
  const hasAdjustment = preview != null && preview.adjustmentAmount !== 0

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('accounts.adjustBalance')}</DialogTitle>
          <DialogDescription>
            {account.type === 'credit_card'
              ? t('accounts.adjustCreditCardBalanceHint')
              : t('accounts.adjustBalanceHint')}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!validTarget || !hasAdjustment) return
            onSave({
              balance: numericTarget,
              exclude_from_pnl: excludeFromReports,
            })
          }}
        >
          <div className="rounded-lg border border-border bg-muted/30 p-3 text-sm">
            <span className="text-muted-foreground">
              {account.type === 'credit_card'
                ? t('accounts.currentRecordedAmountOwed')
                : t('accounts.currentRecordedBalance')}
            </span>
            <span className="float-right font-semibold tabular-nums">
              {formatCurrency(currentInputValue, account.currency, locale)}
            </span>
          </div>

          <div className="space-y-2">
            <Label htmlFor="balance-adjustment-target">
              {account.type === 'credit_card'
                ? t('accounts.actualAmountOwedToday')
                : t('accounts.actualBalanceToday')}
            </Label>
            <Input
              id="balance-adjustment-target"
              type="number"
              step="0.01"
              min={account.type === 'credit_card' ? '0' : undefined}
              value={target}
              onChange={(event) => setTarget(event.target.value)}
              autoFocus
              required
            />
          </div>

          {preview && (
            <div className="rounded-lg border border-border p-3">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="text-muted-foreground">{t('accounts.adjustmentToCreate')}</span>
                <span className={`font-semibold tabular-nums ${
                  preview.adjustmentAmount > 0 ? 'text-emerald-600' : preview.adjustmentAmount < 0 ? 'text-rose-500' : 'text-muted-foreground'
                }`}>
                  {preview.adjustmentAmount > 0 ? '+' : preview.adjustmentAmount < 0 ? '-' : ''}
                  {formatCurrency(Math.abs(preview.adjustmentAmount), account.currency, locale)}
                </span>
              </div>
              {!hasAdjustment && (
                <p className="mt-1 text-xs text-muted-foreground">{t('accounts.noAdjustmentNeeded')}</p>
              )}
            </div>
          )}

          <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border p-3">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 rounded border-border accent-primary"
              checked={excludeFromReports}
              onChange={(event) => setExcludeFromReports(event.target.checked)}
            />
            <span>
              <span className="block text-sm font-medium">
                {t('transactions.excludeFromReports')}
              </span>
              <span className="block text-xs text-muted-foreground">
                {t('transactions.excludeFromReportsHint')}
              </span>
            </span>
          </label>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={loading || !validTarget || !hasAdjustment}>
              {loading ? t('common.loading') : t('accounts.createAdjustment')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
