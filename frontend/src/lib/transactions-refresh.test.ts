import { describe, expect, it, vi } from 'vitest'
import { refreshTransactionsView } from '@/lib/transactions-refresh'

const ok = { isError: false, error: null }

describe('refreshTransactionsView', () => {
  it('refetches the list query in list view', async () => {
    const refetchList = vi.fn().mockResolvedValue(ok)
    const refetchCalendar = vi.fn().mockResolvedValue(ok)
    const onError = vi.fn()
    await refreshTransactionsView({ viewMode: 'list', refetchList, refetchCalendar, onError })
    expect(refetchList).toHaveBeenCalledTimes(1)
    expect(refetchCalendar).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('refetches the calendar query in calendar view', async () => {
    const refetchList = vi.fn().mockResolvedValue(ok)
    const refetchCalendar = vi.fn().mockResolvedValue(ok)
    await refreshTransactionsView({ viewMode: 'calendar', refetchList, refetchCalendar, onError: vi.fn() })
    expect(refetchCalendar).toHaveBeenCalledTimes(1)
    expect(refetchList).not.toHaveBeenCalled()
  })

  it('reports the API error message when the request fails', async () => {
    const error = { response: { data: { detail: 'Account not found' } } }
    const refetchList = vi.fn().mockResolvedValue({ isError: true, error })
    const onError = vi.fn()
    await refreshTransactionsView({ viewMode: 'list', refetchList, refetchCalendar: vi.fn(), onError })
    expect(onError).toHaveBeenCalledWith('Account not found')
  })
})
