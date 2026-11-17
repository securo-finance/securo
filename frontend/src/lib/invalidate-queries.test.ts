import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import { invalidateCategoryQueries, invalidateFinancialQueries } from './invalidate-queries'

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
  it('invalidates goal list and dashboard summary families alongside every existing money view', () => {
    const queryClient = new QueryClient()
    const keys = [
      ['transactions', { page: 1 }],
      ['dashboard', 'summary'],
      ['accounts', 'closed'],
      ['budgets', 'comparison'],
      ['goals'],
      ['goals', 'active'],
      ['goals', 'summary'],
      ['reports', 'net-worth'],
      ['drill-down', { type: 'debit' }],
    ] as const
    for (const queryKey of keys) queryClient.setQueryData(queryKey, [])
    queryClient.setQueryData(['unrelated'], [])

    invalidateFinancialQueries(queryClient)

    for (const queryKey of keys) {
      expect(queryClient.getQueryState(queryKey)?.isInvalidated, JSON.stringify(queryKey)).toBe(true)
    }
    expect(queryClient.getQueryState(['unrelated'])?.isInvalidated).toBe(false)
    queryClient.clear()
  })
})
