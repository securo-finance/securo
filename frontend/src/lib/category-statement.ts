import type { CategoryStatementRow } from '@/types'

export type StatementSort = 'default' | 'asc' | 'desc'
export type StatementSection = 'income' | 'expenses' | 'net'

/**
 * Change from the previous month, oriented so that a positive `percent` means
 * the amount grew: more income, or more spending. `good` says whether that
 * movement helps the user. Null when there is nothing to compare against.
 */
export function statementChange(
  current: number,
  previous: number | undefined,
  section: StatementSection,
): { percent: number; good: boolean } | null {
  if (previous === undefined || previous === 0) return null
  const sign = section === 'expenses' ? -1 : 1
  const percent = ((current - previous) * sign) / Math.abs(previous) * 100
  return { percent, good: section === 'expenses' ? percent < 0 : percent > 0 }
}

export function sortStatementRows(
  rows: CategoryStatementRow[],
  sort: StatementSort,
): CategoryStatementRow[] {
  if (sort === 'default') return rows
  const direction = sort === 'asc' ? 1 : -1
  return [...rows]
    .map((row) => ({ ...row, children: sortStatementRows(row.children, sort) }))
    .sort((a, b) => (Math.abs(a.values[0] ?? 0) - Math.abs(b.values[0] ?? 0)) * direction)
}

export interface StatementSettings {
  sort: StatementSort
  columns: number
  showChange: boolean
}

export const STATEMENT_SETTINGS_KEY = 'securo.reports.statement'
export const STATEMENT_COLUMN_OPTIONS = [1, 2, 3, 4, 5, 6] as const
const DEFAULT_SETTINGS: StatementSettings = { sort: 'default', columns: 3, showChange: true }

export function loadStatementSettings(): StatementSettings {
  try {
    const stored = JSON.parse(localStorage.getItem(STATEMENT_SETTINGS_KEY) ?? 'null')
    if (!stored || typeof stored !== 'object') return DEFAULT_SETTINGS
    return {
      sort: ['default', 'asc', 'desc'].includes(stored.sort) ? stored.sort : DEFAULT_SETTINGS.sort,
      columns: (STATEMENT_COLUMN_OPTIONS as readonly number[]).includes(stored.columns)
        ? stored.columns
        : DEFAULT_SETTINGS.columns,
      showChange: typeof stored.showChange === 'boolean' ? stored.showChange : DEFAULT_SETTINGS.showChange,
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

export function saveStatementSettings(settings: StatementSettings): void {
  try {
    localStorage.setItem(STATEMENT_SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    // Storage can be unavailable (private mode); the choice still applies.
  }
}
