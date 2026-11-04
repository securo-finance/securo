import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { recurring, transactions } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { useAuth } from '@/contexts/auth-context'
import type { SaveAction, TransactionSavePayload } from '@/components/transaction-dialog'
import type { InstallmentSeriesInput, Transaction, TransactionEditPayload } from '@/types'

export type RecurringInput = { frequency: string; end_date?: string }

export type CreateTransactionInput = {
  tx: TransactionEditPayload
  recurringData?: RecurringInput
  installmentData?: InstallmentSeriesInput
  pendingFiles?: File[]
  action?: SaveAction
}

/**
 * The "add transaction" flow shared by every page that opens the
 * TransactionDialog in create mode (transactions, account detail, dashboard),
 * so recurring entries, installment series, pending attachments and the
 * save / save and new / save and duplicate actions behave the same everywhere.
 *
 * Owns the dialog's create-mode draft state (`duplicateDraft`, `formResetKey`)
 * and calls `onDone` when a plain save should close the dialog.
 */
export function useCreateTransaction({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const [duplicateDraft, setDuplicateDraft] = useState<TransactionEditPayload | null>(null)
  const [formResetKey, setFormResetKey] = useState(0)

  // Remount the create form, optionally seeded from a draft.
  const resetForm = (draft: TransactionEditPayload | null = null) => {
    setDuplicateDraft(draft)
    setFormResetKey(k => k + 1)
  }

  const mutation = useMutation({
    mutationFn: async (payload: CreateTransactionInput): Promise<Transaction> => {
      let created: Transaction
      if (payload.installmentData) {
        // Manual installment series: the backend repeats the base row N
        // times with the shared installment fingerprint.
        const series = await transactions.createInstallments(payload.installmentData)
        created = series[0]
      } else {
        created = await transactions.create(payload.tx)
      }
      if (payload.recurringData) {
        await recurring.create({
          description: payload.tx.description,
          amount: payload.tx.amount,
          currency: payload.tx.currency ?? userCurrency,
          type: payload.tx.type,
          frequency: payload.recurringData.frequency,
          start_date: payload.tx.date,
          end_date: payload.recurringData.end_date || undefined,
          category_id: payload.tx.category_id || undefined,
          account_id: payload.tx.account_id || undefined,
          skip_first: true,
        } as Record<string, unknown>)
      }
      if (payload.pendingFiles?.length) {
        await Promise.all(
          payload.pendingFiles.map(file => transactions.attachments.upload(created.id, file))
        )
      }
      return created
    },
    onSuccess: (_created, variables) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      // Goals tracking an account follow its balance.
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      toast.success(t('transactions.created'))
      if (variables.action === 'saveAndNew') {
        resetForm(null)
      } else if (variables.action === 'saveAndDuplicate') {
        resetForm(variables.tx)
      } else {
        onDone()
      }
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  // Same signature as TransactionDialog's onSave, so it can be passed straight through.
  const create = (
    data: TransactionSavePayload,
    recurringData?: RecurringInput,
    installmentData?: InstallmentSeriesInput,
    pendingFiles?: File[],
    action?: SaveAction,
  ) => {
    mutation.mutate({ tx: data, recurringData, installmentData, pendingFiles, action })
  }

  return { mutation, create, duplicateDraft, setDuplicateDraft, formResetKey, resetForm }
}
