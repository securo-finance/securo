import { describe, expect, it } from 'vitest'

import { MODULE_IDS, isModuleId } from '@/lib/modules'

describe('MODULE_IDS', () => {
  it('has no duplicates', () => {
    expect(new Set(MODULE_IDS).size).toBe(MODULE_IDS.length)
  })

  it('lists every module the router gates on', () => {
    // App.tsx wraps routes in <ModuleRoute module="..."> using these exact
    // strings. A rename here without one there renders a permanently
    // blocked page, so pin the set.
    expect([...MODULE_IDS].sort()).toEqual(
      [
        'accounts',
        'assets',
        'budgets',
        'categories',
        'goals',
        'import',
        'invoices',
        'payees',
        'recurring',
        'reports',
        'rules',
        'split_groups',
        'transactions',
      ].sort(),
    )
  })
})

describe('isModuleId', () => {
  it('accepts every known module', () => {
    for (const id of MODULE_IDS) {
      expect(isModuleId(id)).toBe(true)
    }
  })

  it('rejects anything else', () => {
    expect(isModuleId('dashboard')).toBe(false)
    expect(isModuleId('agents')).toBe(false)
    expect(isModuleId('')).toBe(false)
    expect(isModuleId('Transactions')).toBe(false)
  })

  it('is not fooled by Object.prototype members', () => {
    // `includes` is safe here, but a future switch to a plain-object lookup
    // would regress on exactly these.
    expect(isModuleId('constructor')).toBe(false)
    expect(isModuleId('toString')).toBe(false)
  })
})
