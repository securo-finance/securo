import { useQuery } from '@tanstack/react-query'
import { formatCurrency } from '@/lib/format'

interface Entry {
  id: string
  emi_number: number
  due_date: string
  principal_component: number
  interest_component: number
  emi_amount: number
  opening_balance: number
  closing_balance: number
  payment_status: string
}

async function fetchSchedule(accountId: string): Promise<Entry[]> {
  const response = await fetch(`/api/v1/loans/${accountId}/schedule`, {
    headers: {
      Authorization: `Bearer ${localStorage.getItem('token')}`,
      'X-Workspace-Id': localStorage.getItem('workspace_id') || '',
    },
  })
  if (!response.ok) throw new Error('Failed to fetch schedule')
  return response.json()
}

export function LoanScheduleTable({ accountId }: { accountId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['loan-schedule', accountId],
    queryFn: () => fetchSchedule(accountId),
  })
  if (isLoading) return <div className="py-8 text-center text-muted-foreground">Loading schedule…</div>
  if (error) return <div className="py-8 text-center text-destructive">Failed to load schedule</div>
  if (!data?.length) return <div className="py-8 text-center text-muted-foreground">No schedule entries</div>
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr className="text-left">
            <th className="p-2">#</th>
            <th className="p-2">Due</th>
            <th className="p-2">Principal</th>
            <th className="p-2">Interest</th>
            <th className="p-2">EMI</th>
            <th className="p-2">Balance</th>
            <th className="p-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {data.map((e) => (
            <tr key={e.id} className="border-t">
              <td className="p-2">{e.emi_number}</td>
              <td className="p-2">{e.due_date}</td>
              <td className="p-2">{formatCurrency(Number(e.principal_component))}</td>
              <td className="p-2">{formatCurrency(Number(e.interest_component))}</td>
              <td className="p-2">{formatCurrency(Number(e.emi_amount))}</td>
              <td className="p-2">{formatCurrency(Number(e.closing_balance))}</td>
              <td className="p-2 capitalize">{e.payment_status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
