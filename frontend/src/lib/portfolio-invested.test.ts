import { describe, expect, it } from 'vitest'

import { withInvested } from './portfolio-invested'

describe('withInvested', () => {
  it('steps at each point and holds the value until the next one', () => {
    const rows = ['2026-01-01', '2026-01-02', '2026-01-03', '2026-01-06'].map((date) => ({ date }))
    const points = [
      { date: '2026-01-02', amount: 100 },
      { date: '2026-01-05', amount: 40 },
    ]
    expect(withInvested(rows, points).map((r) => r._invested)).toEqual([0, 100, 100, 40])
  })

  it('is 0 everywhere without points and keeps the row fields', () => {
    const rows = [{ date: '2026-01-01', _total: 5 }]
    expect(withInvested(rows, [])).toEqual([{ date: '2026-01-01', _total: 5, _invested: 0 }])
  })
})
