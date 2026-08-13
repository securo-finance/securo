import { describe, expect, it } from 'vitest'

import { appliesCollectionFilter } from './collection-filter-scope'

describe('appliesCollectionFilter', () => {
  it('scopes the pages that read the filter', () => {
    for (const path of ['/', '/transactions', '/accounts', '/reports', '/assets']) {
      expect(appliesCollectionFilter(path), path).toBe(true)
    }
  })

  it('leaves out the pages the filter does not reach', () => {
    for (const path of [
      '/payees',
      '/categories',
      '/rules',
      '/groups',
      '/budgets',
      '/goals',
      '/recurring',
      '/import',
      '/collections',
      '/invoices',
      '/workspace/settings',
      '/admin',
    ]) {
      expect(appliesCollectionFilter(path), path).toBe(false)
    }
  })

  it('does not scope the account detail page', () => {
    // It shows a single account, so a collection filter has nothing to narrow.
    expect(appliesCollectionFilter('/accounts/6f1d0b7e-0000-4000-8000-000000000000')).toBe(false)
  })

  it('treats a trailing slash as the same route', () => {
    expect(appliesCollectionFilter('/accounts/')).toBe(true)
    expect(appliesCollectionFilter('/')).toBe(true)
  })

  it('does not match on prefix', () => {
    // '/reports-archive' is a different page than '/reports'.
    expect(appliesCollectionFilter('/reportsomething')).toBe(false)
    expect(appliesCollectionFilter('/assets-old')).toBe(false)
  })
})
