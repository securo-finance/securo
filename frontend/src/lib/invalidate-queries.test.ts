import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import {
  invalidateCategoryQueries,
  invalidateFinancialQueries,
  invalidateSyncedRecords,
} from './invalidate-queries'

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

describe('invalidateFinancialQueries', () => {
  it('invalidates every financial prefix including both recurring evidence families', () => {
    const queryClient = new QueryClient()
    const affected = [
      ['transactions', { account: 'a1' }],
      ['dashboard', 'summary'],
      ['accounts', 'a1'],
      ['budgets', 'comparison'],
      ['reports', 'cash-flow'],
      ['drill-down', 'transactions'],
      ['recurring', 'recent-price-changes', 20],
      ['recurring', 'recent-price-changes', 1],
      ['recurring', 'price-history', 'rt-1'],
      ['recurring', 'price-history', 'rt-2'],
    ] as const
    // The recurring list is not derived from transaction evidence: an ordinary
    // financial mutation must not refetch it. Only the two history consumers
    // above depend on linked transaction rows.
    const unrelated = [
      ['recurring'],
      ['categories'],
      ['payees'],
      ['recurring-settings'],
    ] as const
    for (const key of [...affected, ...unrelated]) queryClient.setQueryData(key, [])

    invalidateFinancialQueries(queryClient)

    for (const key of affected) {
      expect(queryClient.getQueryState(key)?.isInvalidated, JSON.stringify(key)).toBe(true)
    }
    for (const key of unrelated) {
      expect(queryClient.getQueryState(key)?.isInvalidated, JSON.stringify(key)).toBe(false)
    }
  })
})

describe('invalidateSyncedRecords', () => {
  it('invalidates cash accounts, holdings, their ledgers and performance', () => {
    const queryClient = new QueryClient()
    const keys = [
      ['transactions', 'list'],
      ['dashboard'],
      ['accounts'],
      ['connections'],
      ['assets'],
      ['asset-groups'],
      ['asset-values', 'asset-1'],
      ['asset-trend', 'asset-1'],
      ['asset-transactions', 'asset-1'],
      ['portfolio-trend'],
      ['asset-performance', 'workspace-1'],
      ['holding-trade-import', 'workspace-1'],
      ['import-logs'],
    ] as const

    for (const queryKey of keys) queryClient.setQueryData(queryKey, [])

    invalidateSyncedRecords(queryClient)

    for (const queryKey of keys) {
      expect(queryClient.getQueryState(queryKey)?.isInvalidated).toBe(true)
    }
  })
})
