import { describe, expect, it } from 'vitest'

import { addMonths, displayDue, installmentsTotal, splitEvenly } from './installment-utils'

describe('addMonths', () => {
  it('steps by calendar months and clamps in shorter ones', () => {
    expect(addMonths('2026-01-31', 1)).toBe('2026-02-28')
    expect(addMonths('2026-01-31', 2)).toBe('2026-03-31')
    expect(addMonths('2026-11-15', 2)).toBe('2027-01-15')
    expect(addMonths('2026-03-05', 0)).toBe('2026-03-05')
  })
})

describe('splitEvenly', () => {
  it('divides to the cent and puts the remainder on the last installment', () => {
    const rows = splitEvenly(100, 3, '2026-10-01')
    expect(rows.map((r) => r.amount)).toEqual(['33.33', '33.33', '33.34'])
    expect(rows.map((r) => r.due_date)).toEqual(['2026-10-01', '2026-11-01', '2026-12-01'])
    expect(installmentsTotal(rows)).toBeCloseTo(100, 2)
  })

  it('never makes fewer than two', () => {
    expect(splitEvenly(10, 1, '2026-10-01')).toHaveLength(2)
  })
})

describe('installmentsTotal', () => {
  it('ignores what is not a number yet', () => {
    expect(installmentsTotal([{ due_date: '2026-10-01', amount: '10' }, { due_date: '2026-11-01', amount: '' }])).toBe(10)
  })
})

describe('displayDue', () => {
  it('prefers the next unpaid date while money is owed', () => {
    expect(displayDue({ due_date: '2026-12-01', next_due_date: '2026-10-01' })).toBe('2026-10-01')
    expect(displayDue({ due_date: '2026-12-01', next_due_date: null })).toBe('2026-12-01')
  })
})
