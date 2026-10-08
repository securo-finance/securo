import type { DeductionKind, InstallmentInput, Invoice } from '@/types'

/**
 * Pure helpers for installments and deductions. The server owns every
 * rule (the schedule adds up, money covers it first-to-last); these
 * only shape what the forms send and how the screen reads it.
 */

/** `YYYY-MM-DD` plus N calendar months, day clamped to the target month. */
export function addMonths(iso: string, months: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  const index = m - 1 + months
  const year = y + Math.floor(index / 12)
  const month = (((index % 12) + 12) % 12) + 1
  const lastDay = new Date(year, month, 0).getDate()
  const day = Math.min(d, lastDay)
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

/** A schedule of `count` equal installments a month apart from `first`.
 *  Cents that do not divide land on the last one, so the sum is exact. */
export function splitEvenly(total: number, count: number, first: string): InstallmentInput[] {
  const n = Math.max(2, Math.floor(count))
  const cents = Math.round(total * 100)
  const base = Math.floor(cents / n)
  const rows: InstallmentInput[] = []
  for (let i = 0; i < n; i += 1) {
    const amount = i === n - 1 ? cents - base * (n - 1) : base
    rows.push({ due_date: addMonths(first, i), amount: (amount / 100).toFixed(2), label: null })
  }
  return rows
}

export function installmentsTotal(rows: InstallmentInput[]): number {
  return rows.reduce((sum, row) => {
    const n = Number(row.amount)
    return sum + (Number.isFinite(n) ? n : 0)
  }, 0)
}

export const DEDUCTION_KINDS: DeductionKind[] = ['withholding_tax', 'gateway_fee', 'fx_difference', 'other']

/** What the "Due" figure should say: the next unpaid date while money is
 *  owed, the last one otherwise. */
export function displayDue(invoice: Pick<Invoice, 'due_date' | 'next_due_date'>): string {
  return invoice.next_due_date ?? invoice.due_date
}
