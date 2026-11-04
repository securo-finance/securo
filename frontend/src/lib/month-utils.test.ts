import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  currentMonth,
  monthFromRange,
  monthLabel,
  monthLastDay,
  monthRange,
  shiftMonth,
} from '@/lib/month-utils'

afterEach(() => {
  vi.useRealTimers()
})

describe('currentMonth', () => {
  it('returns the browser-local month, zero padded', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 0, 15))

    expect(currentMonth()).toBe('2026-01')
  })

  it('pads a double-digit month correctly too', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 10, 3))

    expect(currentMonth()).toBe('2026-11')
  })
})

describe('shiftMonth', () => {
  it('steps forward and back within a year', () => {
    expect(shiftMonth('2026-06', 1)).toBe('2026-07')
    expect(shiftMonth('2026-06', -1)).toBe('2026-05')
  })

  it('rolls over the year boundary in both directions', () => {
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
  })

  it('handles multi-month jumps', () => {
    expect(shiftMonth('2026-06', 12)).toBe('2027-06')
    expect(shiftMonth('2026-06', -18)).toBe('2024-12')
  })

  it('returns the same month for a zero shift', () => {
    expect(shiftMonth('2026-06', 0)).toBe('2026-06')
  })
})

describe('monthLastDay', () => {
  it('knows the length of each month', () => {
    expect(monthLastDay('2026-01')).toBe(31)
    expect(monthLastDay('2026-04')).toBe(30)
  })

  it('is leap-year aware', () => {
    expect(monthLastDay('2024-02')).toBe(29)
    expect(monthLastDay('2026-02')).toBe(28)
    // 2000 is a leap year, 1900 is not.
    expect(monthLastDay('2000-02')).toBe(29)
    expect(monthLastDay('1900-02')).toBe(28)
  })
})

describe('monthRange', () => {
  it('spans the whole month as timezone-naive strings', () => {
    expect(monthRange('2026-06')).toEqual({ from: '2026-06-01', to: '2026-06-30' })
    expect(monthRange('2026-01')).toEqual({ from: '2026-01-01', to: '2026-01-31' })
  })

  it('pads a short last day', () => {
    expect(monthRange('2026-02')).toEqual({ from: '2026-02-01', to: '2026-02-28' })
  })

  it('carries no time component, matching the ?from/?to params', () => {
    const { from, to } = monthRange('2026-06')
    expect(from).not.toMatch(/T/)
    expect(to).not.toMatch(/T/)
  })
})

describe('monthLabel', () => {
  it('localises the month name', () => {
    expect(monthLabel('2026-05', 'en-US')).toMatch(/May/)
    expect(monthLabel('2026-05', 'pt-BR')).toMatch(/maio/i)
  })

  it('includes the year', () => {
    expect(monthLabel('2026-05', 'en-US')).toMatch(/2026/)
  })

  it('names the right month at both ends of the year', () => {
    // Building the date on day 2 rather than day 1 is what keeps a negative
    // UTC offset from rolling the label back into the previous month.
    expect(monthLabel('2026-01', 'en-US')).toMatch(/January/)
    expect(monthLabel('2026-12', 'en-US')).toMatch(/December/)
  })
})

describe('monthFromRange', () => {
  it('recognises a range that spans exactly one month', () => {
    expect(monthFromRange('2026-06-01', '2026-06-30')).toBe('2026-06')
    expect(monthFromRange('2024-02-01', '2024-02-29')).toBe('2024-02')
  })

  it('rejects a range that stops short of the month end', () => {
    // A custom range must not make the stepper claim a whole month is
    // selected, or stepping would silently widen the user's filter.
    expect(monthFromRange('2026-06-01', '2026-06-29')).toBeNull()
  })

  it('rejects a range that starts mid-month', () => {
    expect(monthFromRange('2026-06-02', '2026-06-30')).toBeNull()
  })

  it('rejects a range spanning several months', () => {
    expect(monthFromRange('2026-06-01', '2026-07-31')).toBeNull()
  })

  it('returns null when either end is missing', () => {
    expect(monthFromRange(null, '2026-06-30')).toBeNull()
    expect(monthFromRange('2026-06-01', null)).toBeNull()
    expect(monthFromRange(undefined, undefined)).toBeNull()
    expect(monthFromRange('', '')).toBeNull()
  })

  it('round-trips with monthRange', () => {
    const { from, to } = monthRange('2026-11')
    expect(monthFromRange(from, to)).toBe('2026-11')
  })
})
