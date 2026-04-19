import { useState, useEffect, useCallback } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
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
    fx_rate?: number
  }) => void
  loading: boolean
  defaultFromAccountId?: string
}) {
  const { t } = useTranslation()
  const [fromAccountId, setFromAccountId] = useState(defaultFromAccountId || (accounts[0]?.id ?? ''))
  const [toAccountId, setToAccountId] = useState('')
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState(new Date().toISOString().split('T')[0])
  const [description, setDescription] = useState('')
  const [notes, setNotes] = useState('')
  const [fxRate, setFxRate] = useState('')
  const [convertedAmount, setConvertedAmount] = useState('')

  // Reset form when dialog opens
  const resetForm = useCallback(() => {
    setFromAccountId(defaultFromAccountId || (accounts[0]?.id ?? ''))
    setToAccountId('')
    setAmount('')
    setDate(new Date().toISOString().split('T')[0])
    setDescription('')
    setNotes('')
    setFxRate('')
    setConvertedAmount('')
  }, [defaultFromAccountId, accounts])

  useEffect(() => {
    if (open) resetForm()
  }, [open, resetForm])

  const fromAccount = accounts.find((a) => a.id === fromAccountId)
  const toAccount = accounts.find((a) => a.id === toAccountId)
  const isCrossCurrency = fromAccount && toAccount && fromAccount.currency !== toAccount.currency
  const isSameAccount = fromAccountId && toAccountId && fromAccountId === toAccountId

  const availableToAccounts = accounts.filter((a) => a.id !== fromAccountId)

  // Sync fx_rate <-> converted amount when one changes
  const handleFxRateChange = (val: string) => {
    setFxRate(val)
    if (val && amount) {
      setConvertedAmount((parseFloat(amount) * parseFloat(val)).toFixed(2))
    } else {
      setConvertedAmount('')
    }
  }

  const handleConvertedAmountChange = (val: string) => {
    setConvertedAmount(val)
    if (val && amount && parseFloat(amount) > 0) {
      setFxRate((parseFloat(val) / parseFloat(amount)).toFixed(6))
    } else {
      setFxRate('')
    }
  }

  const handleAmountChange = (val: string) => {
    setAmount(val)
    if (fxRate && val) {
      setConvertedAmount((parseFloat(val) * parseFloat(fxRate)).toFixed(2))
    } else if (convertedAmount && val && parseFloat(val) > 0) {
      setFxRate((parseFloat(convertedAmount) / parseFloat(val)).toFixed(6))
    }
  }

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
              fx_rate: isCrossCurrency && fxRate ? parseFloat(fxRate) : undefined,
            })
          }}
          className="space-y-4"
        >
          <div className="grid grid-cols-[1fr,auto,1fr] items-end gap-2">
            <div className="space-y-2">
              <Label>{t('transactions.transferFromAccount')}</Label>
              <select
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                value={fromAccountId}
                onChange={(e) => {
                  setFromAccountId(e.target.value)
                  if (e.target.value === toAccountId) setToAccountId('')
                  setFxRate('')
                  setConvertedAmount('')
                }}
                required
              >
                <option value="" disabled>{t('transactions.account')}</option>
                {accounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {getAccountName(acc)} ({acc.currency})
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
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                value={toAccountId}
                onChange={(e) => {
                  setToAccountId(e.target.value)
                  setFxRate('')
                  setConvertedAmount('')
                }}
                required
              >
                <option value="" disabled>{t('transactions.account')}</option>
                {availableToAccounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {getAccountName(acc)} ({acc.currency})
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
                onChange={(e) => handleAmountChange(e.target.value)}
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
            <div className="space-y-3 p-3 bg-muted/50 border border-border rounded-md">
              <p className="text-xs font-medium text-muted-foreground">
                {t('transactions.conversion')}{' '}
                <span className="font-normal">({t('transactions.conversionHint')})</span>
              </p>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label className="text-xs">
                    {t('transactions.convertedAmount', { currency: toAccount?.currency })}
                  </Label>
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    value={convertedAmount}
                    onChange={(e) => handleConvertedAmountChange(e.target.value)}
                    placeholder={t('transactions.autoCalculated')}
                  />
                </div>
                <div className="space-y-2">
                  <Label className="text-xs">{t('transactions.exchangeRate')}</Label>
                  <Input
                    type="number"
                    step="0.000001"
                    min="0"
                    value={fxRate}
                    onChange={(e) => handleFxRateChange(e.target.value)}
                    placeholder={t('transactions.autoCalculated')}
                  />
                </div>
              </div>
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
              className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background resize-none focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-0"
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
