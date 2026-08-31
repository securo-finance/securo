/**
 * Every route in App.tsx is a `lazy(() => import(...))`. A page that fails to
 * evaluate — a bad export, a circular import, a top-level call to something
 * undefined — does not fail the build, because `tsc` type-checks modules in
 * isolation and Vite only resolves the chunk when a user navigates to it. The
 * first person to find out is whoever clicks the nav link.
 *
 * This walks the same module list the router does and evaluates each one. It
 * asserts only what every page must be: a module whose default export is a
 * component. Cheap, no mocking, and it cannot flake.
 */
import { describe, expect, it } from 'vitest'

const PAGE_MODULES = {
  'account-detail': () => import('@/pages/account-detail'),
  accounts: () => import('@/pages/accounts'),
  'admin/settings': () => import('@/pages/admin/settings'),
  'agent-connections': () => import('@/pages/agent-connections'),
  'agent-detail': () => import('@/pages/agent-detail'),
  'agents-list': () => import('@/pages/agents-list'),
  assets: () => import('@/pages/assets'),
  budgets: () => import('@/pages/budgets'),
  categories: () => import('@/pages/categories'),
  collections: () => import('@/pages/collections'),
  dashboard: () => import('@/pages/dashboard'),
  goals: () => import('@/pages/goals'),
  'group-detail': () => import('@/pages/group-detail'),
  groups: () => import('@/pages/groups'),
  import: () => import('@/pages/import'),
  invoices: () => import('@/pages/invoices'),
  login: () => import('@/pages/login'),
  'oauth-callback': () => import('@/pages/oauth-callback'),
  'oidc-callback': () => import('@/pages/oidc-callback'),
  payees: () => import('@/pages/payees'),
  recurring: () => import('@/pages/recurring'),
  register: () => import('@/pages/register'),
  reports: () => import('@/pages/reports'),
  rules: () => import('@/pages/rules'),
  setup: () => import('@/pages/setup'),
  transactions: () => import('@/pages/transactions'),
  'workspace-settings': () => import('@/pages/workspace-settings'),
} as const

describe('page modules', () => {
  it('covers every page file on disk', () => {
    // Guards the list itself: a new page added without a line here would
    // otherwise never be checked.
    const files = import.meta.glob('@/pages/**/*.tsx')
    const onDisk = Object.keys(files)
      .map((p) => p.replace(/^.*\/pages\//, '').replace(/\.tsx$/, ''))
      .filter((name) => !name.endsWith('.test'))
      .sort()

    expect(onDisk).toEqual(Object.keys(PAGE_MODULES).sort())
  })

  for (const [name, load] of Object.entries(PAGE_MODULES)) {
    it(`${name} evaluates and default-exports a component`, async () => {
      const module = await load()

      expect(module.default).toBeDefined()
      expect(typeof module.default).toBe('function')
    })
  }
})
