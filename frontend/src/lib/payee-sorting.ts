import type { Payee } from '@/types'

export type PayeeSortBy = 'name' | 'type' | 'transaction_count'
export type PayeeSortDirection = 'asc' | 'desc'

export type PayeeSort = {
  by: PayeeSortBy
  direction: PayeeSortDirection
}

export const PAYEE_SORT_STORAGE_KEY = 'securo.payees.sort'
export const DEFAULT_PAYEE_SORT: PayeeSort = { by: 'name', direction: 'asc' }
export const INITIAL_SORT_DIRECTIONS: Record<PayeeSortBy, PayeeSortDirection> = {
  name: 'asc',
  type: 'asc',
  transaction_count: 'desc',
}

export function loadPayeeSort(): PayeeSort {
  try {
    const raw = localStorage.getItem(PAYEE_SORT_STORAGE_KEY)
    if (!raw) return DEFAULT_PAYEE_SORT

    const parsed = JSON.parse(raw)
    if (
      (parsed?.by === 'name' || parsed?.by === 'type' || parsed?.by === 'transaction_count')
      && (parsed?.direction === 'asc' || parsed?.direction === 'desc')
    ) {
      return { by: parsed.by, direction: parsed.direction }
    }
  } catch {
    // Storage may be unavailable or contain data from an older version.
  }
  return DEFAULT_PAYEE_SORT
}

export function sortPayees(
  payees: Payee[],
  sort: PayeeSort,
  locale: string,
  typeLabels: Record<string, string>,
): Payee[] {
  const direction = sort.direction === 'asc' ? 1 : -1
  const collator = new Intl.Collator(locale, { sensitivity: 'base' })

  return [...payees].sort((a, b) => {
    if (sort.by === 'transaction_count') {
      return direction * (a.transaction_count - b.transaction_count)
    }

    if (sort.by === 'type') {
      if (!a.type || !b.type) {
        if (a.type === b.type) return 0
        return a.type ? -direction : direction
      }
      return direction * collator.compare(typeLabels[a.type], typeLabels[b.type])
    }

    return direction * collator.compare(a.name, b.name)
  })
}
