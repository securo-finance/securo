import { describe, expect, it } from 'vitest'

import { localDateString, todayInTimezone, weekdayShortLabels } from './date-utils'

describe('todayInTimezone', () => {
  // 01:00 UTC on May 19 is still May 18 in Sao Paulo and already May 19 in Tokyo.
  const instant = new Date('2026-05-19T01:00:00Z')

  it('returns the calendar day of the given zone', () => {
    expect(todayInTimezone('America/Sao_Paulo', instant)).toBe('2026-05-18')
    expect(todayInTimezone('Asia/Tokyo', instant)).toBe('2026-05-19')
  })

  it('falls back to the browser day without a zone or with an unknown one', () => {
    expect(todayInTimezone(undefined, instant)).toBe(localDateString(instant))
    expect(todayInTimezone('Mars/Olympus_Mons', instant)).toBe(localDateString(instant))
  })
})

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

describe('weekdayShortLabels', () => {
  it('starts the week on Sunday regardless of the viewer timezone', () => {
    const originalTimezone = process.env.TZ

    try {
      process.env.TZ = 'America/Santo_Domingo'
      expect(weekdayShortLabels('en-US')).toEqual(['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'])

      process.env.TZ = 'Pacific/Kiritimati'
      expect(weekdayShortLabels('en-US')).toEqual(['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'])
    } finally {
      if (originalTimezone === undefined) delete process.env.TZ
      else process.env.TZ = originalTimezone
    }
  })

  it('translates the labels for the requested locale', () => {
    expect(weekdayShortLabels('es-ES')[0]).toMatch(/^dom/i)
  })
})
