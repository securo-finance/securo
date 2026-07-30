import { describe, expect, it } from 'vitest'

import { localDateString } from './date-utils'

describe('localDateString', () => {
  it('formats the browser-local calendar day', () => {
    const originalTimezone = process.env.TZ

    try {
      process.env.TZ = 'America/Los_Angeles'
      expect(localDateString(new Date('2026-07-01T06:30:00Z'))).toBe('2026-06-30')
    } finally {
      if (originalTimezone === undefined) delete process.env.TZ
      else process.env.TZ = originalTimezone
    }
  })
})
