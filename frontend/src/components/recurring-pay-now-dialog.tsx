import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { recurringPayments } from '@/lib/api'
import { getAccountName, sortAccountsByDisplayName } from '@/lib/account-utils'
import { extractApiError } from '@/lib/api-errors'
import { localDateString } from '@/lib/date-utils'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { Button } from '@/components/ui/button'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import type { RecurringPaymentInput, RecurringTransaction } from '@/types'

type PayableAccount = { id: string; name: string; display_name?: string | null }

/** Pays a bill's next occurrence now. Open while `recurring` is set. */
export function RecurringPayNowDialog({
  recurring,
  accounts,
  onClose,
}: {
  recurring: RecurringTransaction | null
  accounts: PayableAccount[]
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const payMutation = useMutation({
    mutationFn: ({ id, ...payment }: RecurringPaymentInput & { id: string }) => recurringPayments.pay(id, payment),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      onClose()
      toast.success(t('recurring.paid'))
    },
    onError: (err: unknown) => {
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  return (
    <Dialog
      open={!!recurring}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('recurring.payNow')}</DialogTitle>
        </DialogHeader>
        {recurring && (
          <PayNowForm
            key={recurring.id}
            recurring={recurring}
            accounts={accounts}
            onSave={(payment) => payMutation.mutate({ id: recurring.id, ...payment })}
            onCancel={onClose}
            loading={payMutation.isPending}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function PayNowForm({
  recurring,
  accounts,
  onSave,
  onCancel,
  loading,
}: {
  recurring: RecurringTransaction
  accounts: PayableAccount[]
  onSave: (payment: RecurringPaymentInput) => void
  onCancel: () => void
  loading: boolean
}) {
  const { t } = useTranslation()
  const sortedAccounts = useMemo(() => sortAccountsByDisplayName(accounts), [accounts])
  const [date, setDate] = useState(localDateString())
  const [accountId, setAccountId] = useState(recurring.account_id ?? '')
  const [amount, setAmount] = useState(recurring.amount.toString())
  const [description, setDescription] = useState(recurring.description)

  const selectClass =
    'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        onSave({ date, account_id: accountId, amount: parseFloat(amount), description })
      }}
      className="space-y-4"
    >
      <div className="space-y-2">
        <Label htmlFor="pay-description">{t('recurring.description')}</Label>
        <Input id="pay-description" value={description} onChange={(e) => setDescription(e.target.value)} required />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="pay-amount">{t('recurring.amount')}</Label>
          <Input
            id="pay-amount"
            type="number"
            step="0.01"
            min="0.01"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
          />
        </div>
        <div className="space-y-2">
          <Label>{t('recurring.payDate')}</Label>
          <DatePickerInput value={date} onChange={setDate} className="w-full justify-start" />
        </div>
      </div>
      <div className="space-y-2">
        <Label htmlFor="pay-account">{t('recurring.account')}</Label>
        <select
          id="pay-account"
          className={selectClass}
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
          required
        >
          {!accountId && (
            <option value="" disabled hidden>
              {t('transactions.createCounterpartSelectAccount')}
            </option>
          )}
          {sortedAccounts.map((acc) => (
            <option key={acc.id} value={acc.id}>
              {getAccountName(acc)}
            </option>
          ))}
        </select>
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button type="submit" disabled={loading}>
          {loading ? t('common.loading') : t('recurring.payNow')}
        </Button>
      </DialogFooter>
    </form>
  )
}
