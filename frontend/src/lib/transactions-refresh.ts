import { extractApiError } from '@/lib/api-errors'

interface RefetchResult {
  isError: boolean
  error: unknown
}

export interface RefreshTransactionsViewOptions {
  viewMode: 'list' | 'calendar'
  refetchList: () => Promise<RefetchResult>
  refetchCalendar: () => Promise<RefetchResult>
  onError: (message: string) => void
}

/**
 * Refetch the query behind the view on screen (the calendar renders its own
 * query, not the list) and report a failed request with the API's message.
 */
export async function refreshTransactionsView(
  options: RefreshTransactionsViewOptions,
): Promise<void> {
  const refetch = options.viewMode === 'calendar' ? options.refetchCalendar : options.refetchList
  const result = await refetch()
  if (result.isError) options.onError(extractApiError(result.error))
}
