import { useState, useEffect, useCallback, useMemo } from 'react'
import { getAccountLabel, sortAccountsByDisplayName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { localDateString } from '@/lib/date-utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { ArrowRight, Info } from 'lucide-react'
import type { Account } from '@/types'

export function TransferDialog({
  open,
  onClose,
  accounts,
  onSave,
  loading,
  defaultFromAccountId,
}: {
  open: boolean
  onClose: () => void
  accounts: Account[]
  onSave: (data: {
    from_account_id: string
    to_account_id: string
    amount: number
    date: string
    description: string
    notes?: string
    destination_amount?: number
  }) => void
  loading: boolean
  defaultFromAccountId?: string
}) {
  const { t } = useTranslation()
  const sortedAccounts = useMemo(() => sortAccountsByDisplayName(accounts), [accounts])
  const firstAccountId = sortedAccounts[0]?.id ?? ''
  const [fromAccountId, setFromAccountId] = useState(defaultFromAccountId || firstAccountId)
  const [toAccountId, setToAccountId] = useState('')
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState(localDateString)
  const [description, setDescription] = useState('')
  const [notes, setNotes] = useState('')
  const [destinationAmount, setDestinationAmount] = useState('')

  // Reset form when dialog opens
  const resetForm = useCallback(() => {
    setFromAccountId(defaultFromAccountId || firstAccountId)
    setToAccountId('')
    setAmount('')
    setDate(localDateString())
    setDescription('')
    setNotes('')
    setDestinationAmount('')
  }, [defaultFromAccountId, firstAccountId])

  useEffect(() => {
    if (open) resetForm()
  }, [open, resetForm])

  const fromAccount = accounts.find((a) => a.id === fromAccountId)
  const toAccount = accounts.find((a) => a.id === toAccountId)
  const isCrossCurrency = fromAccount && toAccount && fromAccount.currency !== toAccount.currency
  const isSameAccount = fromAccountId && toAccountId && fromAccountId === toAccountId

  const availableToAccounts = sortedAccounts.filter((a) => a.id !== fromAccountId)

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('transactions.transferTitle')}</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            onSave({
              from_account_id: fromAccountId,
              to_account_id: toAccountId,
              amount: parseFloat(amount),
              date,
              description,
              notes: notes.trim() || undefined,
              destination_amount: isCrossCurrency && destinationAmount
                ? parseFloat(destinationAmount)
                : undefined,
            })
          }}
          className="space-y-4"
        >
          <div className="grid grid-cols-[1fr,auto,1fr] items-end gap-2">
            <div className="space-y-2">
              <Label>{t('transactions.transferFromAccount')}</Label>
              <select
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                value={fromAccountId}
                onChange={(e) => {
                  setFromAccountId(e.target.value)
                  if (e.target.value === toAccountId) setToAccountId('')
                  setDestinationAmount('')
                }}
                required
              >
                <option value="" disabled>{t('transactions.account')}</option>
                {sortedAccounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {getAccountLabel(acc)} ({acc.currency})
                  </option>
                ))}
              </select>
            </div>
            <div className="pb-2">
              <ArrowRight size={18} className="text-muted-foreground" />
            </div>
            <div className="space-y-2">
              <Label>{t('transactions.transferToAccount')}</Label>
              <select
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                value={toAccountId}
                onChange={(e) => {
                  setToAccountId(e.target.value)
                  setDestinationAmount('')
                }}
                required
              >
                <option value="" disabled>{t('transactions.account')}</option>
                {availableToAccounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {getAccountLabel(acc)} ({acc.currency})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {isSameAccount && (
            <div className="flex items-center gap-2 p-3 text-sm bg-destructive/10 text-destructive rounded-md">
              {t('transactions.transferSameAccount')}
            </div>
          )}

          {isCrossCurrency && (
            <div className="flex items-center gap-2 p-3 text-sm bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800 rounded-md text-blue-700 dark:text-blue-300">
              <Info size={14} className="shrink-0" />
              {t('transactions.transferCrossCurrency')}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>
                {t('transactions.transferAmount')}
                {fromAccount && <span className="text-muted-foreground ml-1">({fromAccount.currency})</span>}
              </Label>
              <Input
                type="number"
                step="0.01"
                min="0.01"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label>{t('transactions.date')}</Label>
              <DatePickerInput
                value={date}
                onChange={setDate}
                className="w-full justify-start"
              />
            </div>
          </div>

          {isCrossCurrency && (
            <div className="space-y-2 p-3 bg-muted/50 border border-border rounded-md">
              <Label className="text-xs">
                {t('transactions.convertedAmount', { currency: toAccount?.currency })}
              </Label>
              <Input
                type="number"
                step="0.01"
                min="0.01"
                value={destinationAmount}
                onChange={(e) => setDestinationAmount(e.target.value)}
                placeholder={t('transactions.autoCalculated')}
              />
            </div>
          )}

          <div className="space-y-2">
            <Label>{t('transactions.transferDescription')}</Label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              required
            />
          </div>

          <div className="space-y-2">
            <Label>
              {t('transactions.transferNotes')}{' '}
              <span className="text-muted-foreground font-normal text-xs">({t('transactions.notesHint')})</span>
            </Label>
            <textarea
              className="w-full border border-input rounded-md px-3 py-2 text-sm bg-card resize-none focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-0"
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button
              type="submit"
              disabled={loading || !fromAccountId || !toAccountId || !!isSameAccount}
            >
              {loading ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
