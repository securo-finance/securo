import React, { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { invoices as invoicesApi, payees as payeesApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { toast } from 'sonner'
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { InvoiceCreate, InvoiceRead, InvoiceSummary, Payee } from '@/types'
import { Plus, Trash2, Edit } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { formatCurrency } from '@/lib/format'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'

export default function InvoicesPage() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  
  const [page, setPage] = useState(1)
  const limit = 50

  const { data, isLoading } = useQuery({
    queryKey: ['invoices', page, limit],
    queryFn: () => invoicesApi.list({ page, limit }),
  })

  const { data: payees } = useQuery({
    queryKey: ['payees'],
    queryFn: payeesApi.list,
  })

  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [deletingInvoice, setDeletingInvoice] = useState<InvoiceSummary | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (id: string) => invoicesApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      toast.success(t('invoices.deleted', 'Invoice deleted'))
      setDeletingInvoice(null)
    },
    onError: (err) => {
      toast.error(extractApiError(err))
    },
  })

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'paid': return 'bg-emerald-100 text-emerald-700 border-emerald-200'
      case 'overdue': return 'bg-rose-100 text-rose-700 border-rose-200'
      case 'sent': return 'bg-blue-100 text-blue-700 border-blue-200'
      case 'partial': return 'bg-amber-100 text-amber-700 border-amber-200'
      case 'cancelled': return 'bg-gray-100 text-gray-700 border-gray-200'
      default: return 'bg-slate-100 text-slate-700 border-slate-200'
    }
  }

  return (
    <div>
      <PageHeader 
        section={t('invoices.title', 'Invoices')} 
        title={t('invoices.title', 'Invoices')} 
        action={
          canWrite && (
            <Button onClick={() => setCreateDialogOpen(true)} size="sm">
              <Plus className="h-4 w-4 mr-2" />
              {t('invoices.create', 'New Invoice')}
            </Button>
          )
        }
      />
      
      <div className="p-4 sm:p-8">
        <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('invoices.number', 'Number')}</TableHead>
                  <TableHead>{t('invoices.payee', 'Client')}</TableHead>
                  <TableHead>{t('invoices.issueDate', 'Issue Date')}</TableHead>
                  <TableHead>{t('invoices.dueDate', 'Due Date')}</TableHead>
                  <TableHead className="text-right">{t('invoices.amount', 'Amount')}</TableHead>
                  <TableHead>{t('invoices.status', 'Status')}</TableHead>
                  {canWrite && <TableHead className="w-16"></TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <TableRow>
                    <TableCell colSpan={7} className="text-center py-10 text-muted-foreground">
                      {t('common.loading', 'Loading...')}
                    </TableCell>
                  </TableRow>
                ) : data?.items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="text-center py-10 text-muted-foreground">
                      {t('invoices.empty', 'No invoices found')}
                    </TableCell>
                  </TableRow>
                ) : (
                  data?.items.map(inv => (
                    <TableRow key={inv.id}>
                      <TableCell className="font-medium">{inv.invoice_number}</TableCell>
                      <TableCell>{inv.payee_name || '—'}</TableCell>
                      <TableCell>{inv.issue_date}</TableCell>
                      <TableCell>{inv.due_date}</TableCell>
                      <TableCell className="text-right tabular-nums font-medium">
                        {formatCurrency(inv.total, inv.currency, locale)}
                      </TableCell>
                      <TableCell>
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${getStatusColor(inv.status)} capitalize`}>
                          {inv.status}
                        </span>
                      </TableCell>
                      {canWrite && (
                        <TableCell>
                          <Button 
                            variant="ghost" 
                            size="icon" 
                            className="h-8 w-8 text-muted-foreground hover:text-rose-500"
                            onClick={() => setDeletingInvoice(inv)}
                          >
                            <Trash2 size={16} />
                          </Button>
                        </TableCell>
                      )}
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </div>
      </div>

      <CreateInvoiceDialog 
        open={createDialogOpen} 
        onOpenChange={setCreateDialogOpen} 
        payees={payees ?? []}
      />

      <DeleteConfirmationDialog
        open={deletingInvoice !== null}
        onOpenChange={(o) => !o && setDeletingInvoice(null)}
        title={t('invoices.deleteTitle', 'Delete Invoice')}
        description={t('invoices.deleteDesc', 'Are you sure you want to delete invoice {{number}}? This action cannot be undone.', { number: deletingInvoice?.invoice_number })}
        onConfirm={() => {
          if (deletingInvoice) deleteMutation.mutate(deletingInvoice.id)
        }}
        isDeleting={deleteMutation.isPending}
      />
    </div>
  )
}

function CreateInvoiceDialog({ 
  open, 
  onOpenChange,
  payees
}: { 
  open: boolean
  onOpenChange: (o: boolean) => void 
  payees: Payee[]
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [payeeId, setPayeeId] = useState('')
  const [invoiceNumber, setInvoiceNumber] = useState('')
  const [total, setTotal] = useState('')
  const [issueDate, setIssueDate] = useState('')
  const [dueDate, setDueDate] = useState('')

  const createMutation = useMutation({
    mutationFn: (data: InvoiceCreate) => invoicesApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      toast.success(t('invoices.created', 'Invoice created'))
      onOpenChange(false)
      // reset
      setPayeeId('')
      setInvoiceNumber('')
      setTotal('')
      setIssueDate('')
      setDueDate('')
    },
    onError: (err) => {
      toast.error(extractApiError(err))
    }
  })

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!payeeId || !invoiceNumber || !total || !issueDate || !dueDate) return
    
    createMutation.mutate({
      payee_id: payeeId,
      invoice_number: invoiceNumber,
      currency: 'USD',
      subtotal: parseFloat(total),
      total: parseFloat(total),
      issue_date: issueDate,
      due_date: dueDate,
      status: 'draft',
      line_items: [] // For now, simple creation
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('invoices.create', 'New Invoice')}</DialogTitle>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4 pt-4">
          <div className="space-y-2">
            <Label>{t('invoices.payee', 'Client')}</Label>
            <select 
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              value={payeeId}
              onChange={(e) => setPayeeId(e.target.value)}
              required
            >
              <option value="" disabled>{t('common.select', 'Select...')}</option>
              {payees.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label>{t('invoices.number', 'Invoice Number')}</Label>
            <Input 
              value={invoiceNumber} 
              onChange={e => setInvoiceNumber(e.target.value)} 
              placeholder="INV-001" 
              required
            />
          </div>
          <div className="space-y-2">
            <Label>{t('invoices.amount', 'Amount')}</Label>
            <Input 
              type="number" 
              step="0.01" 
              value={total} 
              onChange={e => setTotal(e.target.value)} 
              required
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>{t('invoices.issueDate', 'Issue Date')}</Label>
              <Input 
                type="date" 
                value={issueDate} 
                onChange={e => setIssueDate(e.target.value)} 
                required
              />
            </div>
            <div className="space-y-2">
              <Label>{t('invoices.dueDate', 'Due Date')}</Label>
              <Input 
                type="date" 
                value={dueDate} 
                onChange={e => setDueDate(e.target.value)} 
                required
              />
            </div>
          </div>
          <DialogFooter className="pt-4">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {t('common.save', 'Save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
