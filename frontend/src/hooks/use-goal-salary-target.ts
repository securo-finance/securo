import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { recurring as recurringApi } from '@/lib/api'
import { defaultSalary, salaryCandidates } from '@/lib/goal-salary'
import type { Goal, SalaryMonths } from '@/types'

/** A goal's target as a multiple of a recurring net salary, next to the fixed amount the goal form has. */
export function useGoalSalaryTarget(editing: Goal | null) {
  const [mode, setMode] = useState<'fixed' | 'salary'>('fixed')
  const [salaryRecurringId, setSalaryRecurringId] = useState<string | null>(null)
  const [months, setMonths] = useState<SalaryMonths>(6)
  const { data: recurringList } = useQuery({
    queryKey: ['recurring'],
    queryFn: recurringApi.list,
  })
  const options = salaryCandidates(recurringList ?? [], editing?.salary_recurring_id ?? null)
  const selected = options.find((r) => r.id === salaryRecurringId) ?? defaultSalary(options)
  const bySalary = mode === 'salary' && selected !== null

  return {
    mode,
    setMode,
    options,
    selected,
    setSalaryRecurringId,
    months,
    setMonths,
    /** Load a goal's target, or the defaults for a new goal. */
    reset(goal: Goal | null) {
      setMode(goal?.salary_recurring_id ? 'salary' : 'fixed')
      setSalaryRecurringId(goal?.salary_recurring_id ?? null)
      setMonths(goal?.salary_months ?? 6)
    },
    /** Spread over the fixed target fields. A salary target replaces them; a fixed one clears the salary. */
    payload: bySalary
      ? {
          salary_recurring_id: selected.id,
          salary_months: months,
          currency: selected.currency,
          target_amount: undefined,
        }
      : { salary_recurring_id: null, salary_months: null },
    blocksSave: mode === 'salary' && selected === null,
  }
}

export type GoalSalaryTarget = ReturnType<typeof useGoalSalaryTarget>
