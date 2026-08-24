import { describe, expect, it } from 'vitest'

import { creditCardCycleBoundaries } from './credit-card-cycle'

describe('creditCardCycleBoundaries', () => {
  it('starts a new cycle on the close date', () => {
    expect(creditCardCycleBoundaries(7, new Date('2026-08-24T12:00:00'))).toEqual({
      start: '2026-08-07',
      end: '2026-09-06',
    })
  })

  it('uses the next month when the reference is the close date', () => {
    expect(creditCardCycleBoundaries(7, new Date('2026-08-07T12:00:00'))).toEqual({
      start: '2026-08-07',
      end: '2026-09-06',
    })
  })
})
