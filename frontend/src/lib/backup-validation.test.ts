import { describe, expect, it } from 'vitest'

import { parsePositiveInteger } from './backup-validation'

describe('parsePositiveInteger', () => {
  it('accepts positive integers', () => {
    expect(parsePositiveInteger('30', 'Invalid')).toBe(30)
  })

  it.each(['', '0', '-1', '1.5', '30d'])('rejects %j', (value) => {
    expect(() => parsePositiveInteger(value, 'Localized error')).toThrow('Localized error')
  })
})
