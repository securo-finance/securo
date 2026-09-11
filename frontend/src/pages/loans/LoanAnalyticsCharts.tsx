import { useQuery } from '@tanstack/react-query'
import { formatCurrency } from '@/lib/format'

async function fetchBreakdown(accountId: string) {
  const response = await fetch(`/api/v1/loans/${accountId}/breakdown?group_by=year`, {
    headers: {
      Authorization: `Bearer ${localStorage.getItem('token')}`,
      'X-Workspace-Id': localStorage.getItem('workspace_id') || '',
    },
  })
  if (!response.ok) throw new Error('Failed to fetch breakdown')
  return response.json() as Promise<Array<{
    period: string
    principal_component: number
    interest_component: number
    total_paid: number
    prepayments: number
  }>>
}

export function LoanAnalyticsCharts({ accountId }: { accountId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['loan-breakdown', accountId],
    queryFn: () => fetchBreakdown(accountId),
  })
  if (isLoading) return <div className="py-8 text-center text-muted-foreground">Loading analytics…</div>
  if (error) return <div className="py-8 text-center text-destructive">Failed to load analytics</div>
  if (!data?.length) return <div className="py-8 text-center text-muted-foreground">No analytics yet</div>
  return (
    <div className="space-y-2">
      {data.map((row) => (
        <div key={row.period} className="flex flex-wrap gap-4 border rounded-md p-3 text-sm">
          <span className="font-medium w-20">{row.period}</span>
          <span>Principal {formatCurrency(row.principal_component)}</span>
          <span>Interest {formatCurrency(row.interest_component)}</span>
          <span>Total {formatCurrency(row.total_paid)}</span>
          {row.prepayments > 0 && <span>Prepay {formatCurrency(row.prepayments)}</span>}
        </div>
      ))}
    </div>
  )
}
