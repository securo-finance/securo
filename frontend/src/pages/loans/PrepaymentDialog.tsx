import { useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { localDateString } from '@/lib/date-utils'

interface Props {
  accountId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess?: () => void
}

export function PrepaymentDialog({ accountId, open, onOpenChange, onSuccess }: Props) {
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState<'reduce_tenure' | 'reduce_emi'>('reduce_tenure')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const response = await fetch('/api/v1/loans/prepayments', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('token')}`,
          'X-Workspace-Id': localStorage.getItem('workspace_id') || '',
        },
        body: JSON.stringify({
          account_id: accountId,
          prepayment_amount: amount,
          prepayment_date: localDateString(new Date()),
          recalculation_method: method,
        }),
      })
      if (!response.ok) throw new Error(await response.text())
      onOpenChange(false)
      onSuccess?.()
    } catch (e: any) {
      setError(e?.message || 'Prepayment failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Make Prepayment</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>Amount</Label>
            <Input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div>
            <Label>Method</Label>
            <select
              className="w-full border rounded-md h-9 px-2 bg-background"
              value={method}
              onChange={(e) => setMethod(e.target.value as any)}
            >
              <option value="reduce_tenure">Reduce tenure</option>
              <option value="reduce_emi">Reduce EMI</option>
            </select>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={busy || !amount} onClick={submit}>{busy ? 'Saving…' : 'Apply'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
