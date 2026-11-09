import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import { invalidateCategoryQueries, invalidateConnectionSyncQueries } from './invalidate-queries'

describe('invalidateCategoryQueries', () => {
  it('invalidates category displays and both category-group key conventions', () => {
    const queryClient = new QueryClient()
    const keys = [
      ['categories'],
      ['categories', 'management'],
      ['categoryGroups'],
      ['categoryGroups', 'management'],
      ['category-groups'],
      ['category-groups', 'management'],
    ] as const

    for (const queryKey of keys) queryClient.setQueryData(queryKey, [])

    invalidateCategoryQueries(queryClient)

    for (const queryKey of keys) {
      expect(queryClient.getQueryState(queryKey)?.isInvalidated).toBe(true)
    }
  })
})

describe('invalidateConnectionSyncQueries', () => {
  it('invalidates cash accounts, investment data, and import progress', () => {
    const queryClient = new QueryClient()
    const keys = [
      ['transactions', 'list'],
      ['dashboard'],
      ['accounts'],
      ['budgets'],
      ['reports'],
      ['drill-down'],
      ['connections'],
      ['assets'],
      ['asset-groups'],
      ['asset-performance'],
      ['holding-trade-import'],
      ['portfolio-trend'],
      ['import-logs'],
    ] as const

    for (const queryKey of keys) queryClient.setQueryData(queryKey, [])

    invalidateConnectionSyncQueries(queryClient)

    for (const queryKey of keys) {
      expect(queryClient.getQueryState(queryKey)?.isInvalidated).toBe(true)
    }
  })
})
