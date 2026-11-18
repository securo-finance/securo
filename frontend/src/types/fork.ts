// This fork's types. types/index.ts re-exports them in one line, and the
// fields the fork adds to upstream interfaces are declared below, so the
// upstream file stays close to upstream's.

export interface RecurringPaymentInput {
  date: string
  account_id: string
  amount: number
  description: string
}

export interface SpendingPlan {
  daily_allowance: string
  balance_floor: string
  account_ids: string[] | null
  horizon_months: number
}

export interface SpendingPlanInput {
  daily_allowance: number
  balance_floor: number
  account_ids: string[] | null
  horizon_months: number
}

export interface SimulatedPurchase {
  description?: string
  amount: number
  installments: number
  first_date: string
}

export type SpendingPlanEventSource = 'recurring' | 'planned' | 'card' | 'other' | 'scenario'

/** One move behind the projected balance. A card charge sits on its bill's due date. */
export interface SpendingPlanEvent {
  date: string
  /** Null for "other": the part of a day's move no row names. */
  description: string | null
  /** Signed, in the primary currency. */
  amount: number
  source: SpendingPlanEventSource
  account_name: string | null
}

export interface SpendingPlanTimeline {
  currency: string
  daily_allowance: number
  balance_floor: number
  horizon_end: string
  safe_daily_allowance: number
  average_daily_surplus: number
  covered_until: string | null
  covered_through_horizon: boolean
  /** Balance at the close of yesterday, before today's moves. */
  opening_balance: number
  /** The day the safe allowance is computed through: the tightest one ahead. */
  binding_date: string | null
  today: { allowance: number; spent: number; remaining: number }
  points: { date: string; balance: number }[]
  /** The events on a day sum to that day's change in balance before the allowance. */
  events: SpendingPlanEvent[]
}

export type SalaryMonths = 3 | 6 | 12

declare module './index' {
  interface TransactionCalendarDay {
    projected_allowance?: number
  }

  interface TransactionCalendarResponse {
    spending_plan_applied?: boolean
  }

  interface Goal {
    salary_recurring_id: string | null
    salary_months: SalaryMonths | null
    salary_name: string | null
    monthly_salary: number | null
  }

  interface GoalSummary {
    salary_months: SalaryMonths | null
    monthly_salary: number | null
  }
}
