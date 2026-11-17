import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { CategorySelect } from '@/components/category-select'
import { Button } from '@/components/ui/button'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useEffectiveTimezone } from '@/hooks/use-timezone'
import { accounts, categories as categoriesApi, categoryGroups as categoryGroupsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { todayInTimezone } from '@/lib/date-utils'
import { formatCurrency } from '@/lib/format'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'

/**
 * Brings a loan's ledger in line with the lender's statement. Indexed or
 * renegotiated contracts move in ways the schedule cannot compute; the
 * backend records the difference as one transaction on the loan.
 */
export function LoanBalanceDialog({
  open,
  onClose,
  accountId,
  currentDebt,
  currency,
  locale,
}: {
  open: boolean
  onClose: () => void
  accountId: string
  currentDebt: number
  currency: string
  locale: string
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  // The backend checks the date against today in the workspace timezone.
  const today = todayInTimezone(useEffectiveTimezone())
  const [balance, setBalance] = useState(String(currentDebt))
  // Follows the workspace date until the user picks one.
  const [pickedDate, setPickedDate] = useState<string | null>(null)
  const date = pickedDate ?? today
  const [description, setDescription] = useState(t('loans.adjustDefaultDescription'))
  const [categoryId, setCategoryId] = useState('')

  const { data: categoryList = [] } = useQuery({ queryKey: ['categories'], queryFn: categoriesApi.list, enabled: open })
  const { data: groupList = [] } = useQuery({ queryKey: ['categoryGroups'], queryFn: categoryGroupsApi.list, enabled: open })

  const money = (value: number) => formatCurrency(value, currency, locale)
  const statement = balance.trim() === '' ? NaN : Number(balance)
  const valid = Number.isFinite(statement) && statement >= 0 && description.trim() !== '' && date !== ''
  // The ledger is only known for today here; on another date the backend
  // compares with the balance on that day.
  const difference = valid && date === today ? Math.round((statement - currentDebt) * 100) / 100 : null

  const mutation = useMutation({
    mutationFn: () =>
      accounts.adjustLoanBalance(accountId, {
        balance: statement,
        date,
        description: description.trim(),
        category_id: categoryId || null,
      }),
    onSuccess: (result) => {
      invalidateFinancialQueries(queryClient)
      toast.success(
        result.adjustment === 0
          ? t('loans.adjustMatched')
          : t('loans.adjustRecorded', { amount: money(Math.abs(result.adjustment)) }),
      )
      onClose()
    },
    onError: (error) => toast.error(extractApiError(error, t('common.error'))),
  })

  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('loans.adjustTitle')}</DialogTitle>
          <DialogDescription>{t('loans.adjustHint')}</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (valid) mutation.mutate()
          }}
        >
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="loan-statement-balance">{t('loans.adjustBalance')}</Label>
              <Input
                id="loan-statement-balance"
                type="number"
                step="0.01"
                min="0"
                value={balance}
                onChange={(event) => setBalance(event.target.value)}
                required
              />
            </div>
            <div className="space-y-1">
              <Label>{t('loans.adjustDate')}</Label>
              <DatePickerInput value={date} onChange={setPickedDate} />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="loan-adjust-description">{t('loans.adjustDescription')}</Label>
            <Input
              id="loan-adjust-description"
              value={description}
              maxLength={500}
              onChange={(event) => setDescription(event.target.value)}
              required
            />
          </div>
          <div className="space-y-1">
            <Label>{t('loans.adjustCategory')}</Label>
            <CategorySelect
              value={categoryId}
              onChange={setCategoryId}
              categories={categoryList}
              groups={groupList}
              allowNone
              creatable
            />
          </div>
          <p className="text-xs text-muted-foreground" role="status">
            {difference === null
              ? t('loans.adjustOnDate')
              : difference > 0
                ? t('loans.adjustPreviewGrew', { amount: money(difference) })
                : difference < 0
                  ? t('loans.adjustPreviewFell', { amount: money(-difference) })
                  : t('loans.adjustPreviewMatches')}
          </p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!valid || mutation.isPending}>
              {mutation.isPending ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
