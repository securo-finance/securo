import { describe, expect, it } from 'vitest'

import { formatReimbursementNet, isFullyAllocatedReimbursement } from '@/lib/reimbursements'

const anchor = { type: 'debit', currency: 'EUR', reimbursement_role: 'anchor' } as const

describe('reimbursement display helpers', () => {
  it('signs an anchor net with its direction, and flips it once paid back in full', () => {
    expect(formatReimbursementNet({ ...anchor, reimbursement_net: 34 }, 'en-US')).toBe('−€34.00')
    expect(formatReimbursementNet({ ...anchor, reimbursement_net: -50 }, 'en-US')).toBe('+€50.00')
    expect(formatReimbursementNet({ ...anchor, reimbursement_net: 0 }, 'en-US')).toBe('€0.00')
    expect(formatReimbursementNet({ ...anchor, type: 'credit', reimbursement_net: 50 }, 'en-US')).toBe('+€50.00')
  })

  it('has no net for a counterpart or an unconverted anchor', () => {
    expect(formatReimbursementNet({ ...anchor, reimbursement_net: null }, 'en-US')).toBeNull()
    expect(formatReimbursementNet({ ...anchor, reimbursement_role: 'counterpart', reimbursement_net: 5 }, 'en-US')).toBeNull()
  })

  it('treats a counterpart with nothing left as fully linked', () => {
    const counterpart = { type: 'credit', currency: 'EUR', reimbursement_role: 'counterpart' } as const
    expect(isFullyAllocatedReimbursement({ ...counterpart, reimbursement_unallocated: 0 })).toBe(true)
    expect(isFullyAllocatedReimbursement({ ...counterpart, reimbursement_unallocated: 12 })).toBe(false)
    expect(isFullyAllocatedReimbursement({ ...anchor, reimbursement_unallocated: 0 })).toBe(false)
  })
})
