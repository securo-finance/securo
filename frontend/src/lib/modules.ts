/**
 * Mirror of the backend's `ModuleId` (`services/module_service.py`).
 *
 * This file names the modules. It never decides which ones a workspace
 * gets — that is resolved server-side and arrives as
 * `workspace.enabled_modules`. Two copies of the rule would drift, and
 * the drift shows up as a user seeing a module the server thinks is off.
 */
export const MODULE_IDS = [
  'transactions',
  'accounts',
  'import',
  'reports',
  'assets',
  'budgets',
  'goals',
  'recurring',
  'categories',
  'payees',
  'split_groups',
  'rules',
  'invoices',
] as const

export type ModuleId = (typeof MODULE_IDS)[number]

export function isModuleId(value: string): value is ModuleId {
  return (MODULE_IDS as readonly string[]).includes(value)
}
