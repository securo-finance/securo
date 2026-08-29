import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { reconciliation as reconciliationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { CheckCircle2, ArrowRight } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { formatCurrency } from '@/lib/format'
import type { ReconciliationMatch, ReconciliationSuggestion } from '@/types'

export default function ReconciliationPage() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()

  const { data: suggestions, isLoading } = useQuery({
    queryKey: ['reconciliation', 'suggestions'],
    queryFn: () => reconciliationApi.suggestions(),
  })

  const applyMutation = useMutation({
    mutationFn: (payload: { transaction_id: string; match_type: string; expected_id: string }) => 
      reconciliationApi.apply(payload),
    onSuccess: () => {
      toast.success(t('reconciliation.applied', 'Match applied successfully'))
      queryClient.invalidateQueries({ queryKey: ['reconciliation'] })
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
    },
    onError: (err) => {
      toast.error(extractApiError(err))
    }
  })

  return (
    <div>
      <PageHeader 
        section={t('reconciliation.title', 'Payment Reconciliation')} 
        title={t('reconciliation.title', 'Payment Reconciliation')} 
      />
      
      <div className="p-4 sm:p-8 max-w-4xl mx-auto space-y-6">
        <div className="mb-4">
          <p className="text-muted-foreground text-sm">
            {t('reconciliation.description', 'Review imported bank transactions against expected recurring payments and open invoices.')}
          </p>
        </div>

        {isLoading ? (
          <div className="text-center py-12 text-muted-foreground">
            {t('common.loading', 'Loading...')}
          </div>
        ) : !suggestions || suggestions.length === 0 ? (
          <div className="bg-card rounded-xl border p-12 text-center flex flex-col items-center">
            <CheckCircle2 className="h-12 w-12 text-emerald-500 mb-4" />
            <h3 className="text-lg font-semibold">{t('reconciliation.allCaughtUp', "You're all caught up!")}</h3>
            <p className="text-muted-foreground mt-2 max-w-sm">
              {t('reconciliation.noPending', 'There are no unmatched synced transactions that look like expected recurring bills or invoices.')}
            </p>
          </div>
        ) : (
          <div className="space-y-6">
            {suggestions.map((suggestion) => (
              <SuggestionCard 
                key={suggestion.transaction.id} 
                suggestion={suggestion} 
                locale={locale}
                onApply={(match) => applyMutation.mutate({
                  transaction_id: suggestion.transaction.id,
                  match_type: match.match_type,
                  expected_id: match.expected_id
                })}
                isApplying={applyMutation.isPending}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function SuggestionCard({ 
  suggestion, 
  locale,
  onApply,
  isApplying 
}: { 
  suggestion: ReconciliationSuggestion
  locale: string
  onApply: (match: ReconciliationMatch) => void
  isApplying: boolean
}) {
  const { t } = useTranslation()
  const tx = suggestion.transaction

  return (
    <div className="bg-card rounded-xl border shadow-sm overflow-hidden flex flex-col md:flex-row">
      <div className="p-5 md:w-1/2 md:border-r border-b md:border-b-0 bg-muted/30">
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">
          {t('reconciliation.syncedTx', 'Bank Transaction')}
        </div>
        <div className="flex justify-between items-start mb-1">
          <div className="font-medium text-base truncate pr-4">{tx.description}</div>
          <div className={`font-semibold tabular-nums whitespace-nowrap ${tx.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
            {tx.type === 'credit' ? '+' : ''}{formatCurrency(tx.amount, 'USD', locale)}
          </div>
        </div>
        <div className="text-sm text-muted-foreground">
          {tx.date}
        </div>
      </div>
      
      <div className="p-5 md:w-1/2 relative">
        <div className="hidden md:flex absolute -left-3 top-1/2 -translate-y-1/2 bg-background border rounded-full p-1 text-muted-foreground z-10">
          <ArrowRight size={14} />
        </div>
        
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">
          {t('reconciliation.suggestedMatch', 'Suggested Match')}
        </div>
        
        <div className="space-y-3">
          {suggestion.matches.map((match, idx) => (
            <div key={idx} className="flex justify-between items-center p-3 rounded-lg border bg-background hover:border-primary/50 transition-colors">
              <div className="overflow-hidden pr-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded-sm ${
                    match.match_type === 'invoice' ? 'bg-blue-100 text-blue-700' : 'bg-purple-100 text-purple-700'
                  }`}>
                    {match.match_type}
                  </span>
                  {match.confidence === 'high' && (
                    <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded-sm bg-emerald-100 text-emerald-700">
                      High match
                    </span>
                  )}
                </div>
                <div className="font-medium text-sm truncate">{match.expected_description}</div>
                <div className="text-xs text-muted-foreground flex gap-2 mt-0.5">
                  <span>{match.expected_date}</span>
                  <span>•</span>
                  <span className="tabular-nums font-medium">{formatCurrency(match.expected_amount, 'USD', locale)}</span>
                </div>
              </div>
              <Button 
                size="sm" 
                variant={idx === 0 ? "default" : "outline"}
                disabled={isApplying}
                onClick={() => onApply(match)}
              >
                {t('reconciliation.apply', 'Apply')}
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
