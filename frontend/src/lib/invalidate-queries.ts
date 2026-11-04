import type { QueryClient } from '@tanstack/react-query'

// Invalidate every "money view" query that can reflect the outcome of a
// financial mutation — transaction list, dashboard summary/totals/charts,
// account balances (sidebar + accounts page), budget comparison, and any
// open drill-down overlay. Call this from every mutation that creates/
// updates/deletes a transaction, transfer, or anything that shifts account
// balances. Pages that also need to refresh entity-specific lists
// (recurring, payees, connections, etc.) should invalidate those keys
// on top of this call.
export function invalidateCategoryQueries(queryClient: QueryClient) {
  queryClient.invalidateQueries({ queryKey: ['categories'] })
  // Both key families are currently used across the frontend. Invalidate both
  // until callers have been migrated to a single convention.
  queryClient.invalidateQueries({ queryKey: ['categoryGroups'] })
  queryClient.invalidateQueries({ queryKey: ['category-groups'] })
}

export function invalidateFinancialQueries(queryClient: QueryClient) {
  queryClient.invalidateQueries({ queryKey: ['transactions'] })
  queryClient.invalidateQueries({ queryKey: ['dashboard'] })
  queryClient.invalidateQueries({ queryKey: ['accounts'] })
  queryClient.invalidateQueries({ queryKey: ['budgets'] })
  queryClient.invalidateQueries({ queryKey: ['reports'] })
  queryClient.invalidateQueries({ queryKey: ['drill-down'] })
}
