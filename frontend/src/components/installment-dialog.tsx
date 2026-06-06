import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { Account, ManualInstallmentCreate } from '@/types'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: ManualInstallmentCreate) => void
  isLoading: boolean
  accounts: Account[]
  initialData?: {
    merchant_name?: string
    account_id?: string
    total_amount?: number
    total_installments?: number
    purchase_date?: string
    monthly_amount?: number
    notes?: string
  }
}

export function InstallmentDialog({
  open,
  onOpenChange,
  onSubmit,
  isLoading,
  accounts,
  initialData,
}: Props) {
  const { t } = useTranslation()
  const [merchantName, setMerchantName] = useState('')
  const [accountId, setAccountId] = useState('')
  const [totalAmount, setTotalAmount] = useState('')
  const [totalInstallments, setTotalInstallments] = useState('')
  const [purchaseDate, setPurchaseDate] = useState('')
  const [monthlyAmount, setMonthlyAmount] = useState('')
  const [notes, setNotes] = useState('')

  useEffect(() => {
    if (open) {
      if (initialData) {
        setMerchantName(initialData.merchant_name || '')
        setAccountId(initialData.account_id || '')
        setTotalAmount(initialData.total_amount?.toString() || '')
        setTotalInstallments(initialData.total_installments?.toString() || '')
        setPurchaseDate(initialData.purchase_date || '')
        setMonthlyAmount(initialData.monthly_amount?.toString() || '')
        setNotes(initialData.notes || '')
      } else {
        setMerchantName('')
        setAccountId('')
        setTotalAmount('')
        setTotalInstallments('')
        setPurchaseDate(new Date().toISOString().split('T')[0])
        setMonthlyAmount('')
        setNotes('')
      }
    }
  }, [open, initialData])

  useEffect(() => {
    const total = parseFloat(totalAmount)
    const count = parseInt(totalInstallments, 10)
    if (total > 0 && count > 0 && !monthlyAmount) {
      setMonthlyAmount((total / count).toFixed(2))
    }
  }, [totalAmount, totalInstallments])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSubmit({
      merchant_name: merchantName,
      account_id: accountId,
      total_amount: parseFloat(totalAmount),
      total_installments: parseInt(totalInstallments, 10),
      purchase_date: purchaseDate,
      monthly_amount: monthlyAmount ? parseFloat(monthlyAmount) : null,
      notes: notes || null,
    })
  }

  const creditAccounts = accounts.filter((a) => a.type === 'credit_card')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {initialData
              ? t('installments.dialog.edit', 'Edit Installment')
              : t('installments.dialog.create', 'New Installment')}
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="merchant">{t('installments.dialog.merchant', 'Merchant')}</Label>
            <Input
              id="merchant"
              value={merchantName}
              onChange={(e) => setMerchantName(e.target.value)}
              required
            />
          </div>

          <div className="space-y-2">
            <Label>{t('installments.dialog.account', 'Account')}</Label>
            <Select value={accountId} onValueChange={setAccountId} required>
              <SelectTrigger>
                <SelectValue placeholder={t('installments.dialog.selectAccount', 'Select account')} />
              </SelectTrigger>
              <SelectContent>
                {creditAccounts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.display_name || a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="total">{t('installments.dialog.totalAmount', 'Total Amount')}</Label>
              <Input
                id="total"
                type="number"
                step="0.01"
                min="0"
                value={totalAmount}
                onChange={(e) => setTotalAmount(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="installments">{t('installments.dialog.installments', 'Installments')}</Label>
              <Input
                id="installments"
                type="number"
                min="2"
                max="60"
                value={totalInstallments}
                onChange={(e) => setTotalInstallments(e.target.value)}
                required
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="date">{t('installments.dialog.purchaseDate', 'Purchase Date')}</Label>
              <Input
                id="date"
                type="date"
                value={purchaseDate}
                onChange={(e) => setPurchaseDate(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="monthly">{t('installments.dialog.monthlyAmount', 'Monthly Amount')}</Label>
              <Input
                id="monthly"
                type="number"
                step="0.01"
                min="0"
                value={monthlyAmount}
                onChange={(e) => setMonthlyAmount(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="notes">{t('installments.dialog.notes', 'Notes')}</Label>
            <Input
              id="notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={isLoading}>
              {t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
