import type { RecurringTransaction, SalaryMonths } from '@/types'
import { PERIODS_PER_YEAR } from '@/lib/invoice-schedule-utils'

export const SALARY_MONTHS: SalaryMonths[] = [3, 6, 12]

/** Unrounded, so the target matches the server's months x monthly figure. */
export function monthlySalary(recurring: Pick<RecurringTransaction, 'amount' | 'frequency'>): number {
  return (Number(recurring.amount) * PERIODS_PER_YEAR[recurring.frequency]) / 12
}

export function salaryTarget(
  recurring: Pick<RecurringTransaction, 'amount' | 'frequency'>,
  months: SalaryMonths,
): number {
  return Math.round(monthlySalary(recurring) * months * 100) / 100
}

/** Active recurring incomes a salary goal can follow. The goal being edited
 *  keeps its own salary in the list even if that recurring was paused. */
export function salaryCandidates(
  recurring: RecurringTransaction[],
  keepId: string | null = null,
): RecurringTransaction[] {
  return recurring.filter((r) => r.type === 'credit' && (r.is_active || r.id === keepId))
}

/** The income named like a salary, else the largest monthly income. */
export function defaultSalary(candidates: RecurringTransaction[]): RecurringTransaction | null {
  const named = candidates.find((r) => /sal[aá]ri/i.test(r.description))
  if (named) return named
  return candidates.reduce<RecurringTransaction | null>(
    (best, r) => (best && monthlySalary(best) >= monthlySalary(r) ? best : r),
    null,
  )
}
