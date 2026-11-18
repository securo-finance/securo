import api from '@/lib/api'
import type {
  RecurringPaymentInput,
  RecurringTransaction,
  SimulatedPurchase,
  SpendingPlan,
  SpendingPlanInput,
  SpendingPlanTimeline,
  Transaction,
} from '@/types'

// This fork's endpoints. lib/api.ts re-exports them in one line, so callers
// and test mocks keep importing from '@/lib/api' while the upstream file
// stays close to upstream's.

export const recurringPayments = {
  pay: async (
    id: string,
    payment: RecurringPaymentInput,
  ): Promise<{ transaction: Transaction; recurring: RecurringTransaction }> => {
    const { data } = await api.post(`/recurring-transactions/${id}/pay`, payment)
    return data
  },
}

export const spendingPlan = {
  get: async (): Promise<SpendingPlan | null> => {
    const { data } = await api.get('/spending-plan')
    return data
  },
  save: async (plan: SpendingPlanInput): Promise<SpendingPlan> => {
    const { data } = await api.put('/spending-plan', plan)
    return data
  },
  remove: async (): Promise<void> => {
    await api.delete('/spending-plan')
  },
  timeline: async (): Promise<SpendingPlanTimeline> => {
    const { data } = await api.get('/spending-plan/timeline')
    return data
  },
  // The timeline for a plan being edited. Saves nothing.
  preview: async (draft: SpendingPlanInput): Promise<SpendingPlanTimeline> => {
    const { data } = await api.post('/spending-plan/preview', draft)
    return data
  },
  // The timeline after hypothetical purchases. Saves nothing.
  simulate: async (purchases: SimulatedPurchase[], dailyAllowance?: number): Promise<SpendingPlanTimeline> => {
    const { data } = await api.post('/spending-plan/simulate', {
      purchases,
      ...(dailyAllowance === undefined ? {} : { daily_allowance: dailyAllowance }),
    })
    return data
  },
}
