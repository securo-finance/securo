import type { QueryClient } from '@tanstack/react-query'

/** Everything that reads the plan: this page, the dashboard strip and the transaction calendar. */
export function invalidatePlanQueries(queryClient: QueryClient) {
  queryClient.invalidateQueries({ queryKey: ['spending-plan'] })
  queryClient.invalidateQueries({ queryKey: ['dashboard', 'spending-plan'] })
  queryClient.invalidateQueries({ queryKey: ['transactions', 'calendar'] })
}
