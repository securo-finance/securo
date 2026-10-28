import { describe, expect, it } from 'vitest'
import {
  addDaysIso,
  addMonthsIso,
  alignedSpanMonths,
  daysBetween,
  periodWindow,
  rollingStart,
  windowMonths,
} from './report-period'

describe('addDaysIso', () => {
  it('shifts within a month', () => {
    expect(addDaysIso('2026-09-12', -29)).toBe('2026-08-14')
    expect(addDaysIso('2026-09-12', 10)).toBe('2026-09-22')
  })

  it('rolls across month and year boundaries', () => {
    expect(addDaysIso('2026-01-01', -1)).toBe('2025-12-31')
    expect(addDaysIso('2025-12-31', 1)).toBe('2026-01-01')
  })
})

describe('addMonthsIso', () => {
  it('clamps the day to the target month length', () => {
    expect(addMonthsIso('2026-01-31', -1)).toBe('2025-12-31')
    expect(addMonthsIso('2026-03-31', -1)).toBe('2026-02-28')
    expect(addMonthsIso('2024-03-31', -1)).toBe('2024-02-29')
  })

  it('rolls across year boundaries', () => {
    expect(addMonthsIso('2026-12-15', 1)).toBe('2027-01-15')
    expect(addMonthsIso('2026-01-15', -1)).toBe('2025-12-15')
  })
})

describe('daysBetween', () => {
  it('counts whole days from one date to another', () => {
    expect(daysBetween('2026-01-01', '2026-01-01')).toBe(0)
    expect(daysBetween('2026-01-01', '2026-01-31')).toBe(30)
    expect(daysBetween('2026-01-31', '2026-01-01')).toBe(-30)
  })
})

describe('rollingStart', () => {
  it('snaps the current month back by months * 30 days to the 1st', () => {
    expect(rollingStart('2026-09-15', 1)).toBe('2026-08-01')
    expect(rollingStart('2026-09-15', 6)).toBe('2026-03-01')
  })
})

describe('windowMonths', () => {
  it('rounds the span up to months, clamped to 1..24', () => {
    expect(windowMonths({ from: '2026-09-01', to: '2026-09-30' })).toBe(1)
    expect(windowMonths({ from: '2026-01-01', to: '2026-12-31' })).toBe(13)
    expect(windowMonths({ from: '2022-01-01', to: '2026-12-31' })).toBe(24)
  })
})

describe('periodWindow: month presets', () => {
  const today = '2026-09-15'

  it('This Month at offset 0 spans the 1st to today', () => {
    expect(periodWindow({ kind: 'month', anchorDelta: 0 }, today, 0)).toEqual({
      from: '2026-09-01',
      to: '2026-09-15',
    })
  })

  it('This Month steps back to whole previous months', () => {
    expect(periodWindow({ kind: 'month', anchorDelta: 0 }, today, -1)).toEqual({
      from: '2026-08-01',
      to: '2026-08-31',
    })
  })

  it('Last Month at offset 0 is the whole previous month', () => {
    expect(periodWindow({ kind: 'month', anchorDelta: -1 }, today, 0)).toEqual({
      from: '2026-08-01',
      to: '2026-08-31',
    })
  })

  it('Last Month steps back through earlier months', () => {
    expect(periodWindow({ kind: 'month', anchorDelta: -1 }, today, -1)).toEqual({
      from: '2026-07-01',
      to: '2026-07-31',
    })
  })

  it('crosses the year boundary', () => {
    expect(periodWindow({ kind: 'month', anchorDelta: -1 }, '2026-01-15', 0)).toEqual({
      from: '2025-12-01',
      to: '2025-12-31',
    })
    expect(periodWindow({ kind: 'month', anchorDelta: 0 }, '2026-01-15', 0)).toEqual({
      from: '2026-01-01',
      to: '2026-01-15',
    })
  })
})

describe('periodWindow: ytd', () => {
  it('offset 0 defers to the API period=ytd path', () => {
    expect(periodWindow({ kind: 'ytd' }, '2026-09-15', 0)).toBeNull()
  })

  it('negative offsets span whole previous years', () => {
    expect(periodWindow({ kind: 'ytd' }, '2026-09-15', -1)).toEqual({
      from: '2025-01-01',
      to: '2025-12-31',
    })
    expect(periodWindow({ kind: 'ytd' }, '2026-09-15', -2)).toEqual({
      from: '2024-01-01',
      to: '2024-12-31',
    })
  })

  it('follows a financial year that starts in April', () => {
    expect(periodWindow({ kind: 'ytd', fyStartMonth: 4 }, '2026-09-15', -1)).toEqual({
      from: '2025-04-01',
      to: '2026-03-31',
    })
    // Before April the current financial year began the previous calendar year.
    expect(periodWindow({ kind: 'ytd', fyStartMonth: 4 }, '2026-02-10', -1)).toEqual({
      from: '2024-04-01',
      to: '2025-03-31',
    })
  })
})

describe('periodWindow: rolling presets', () => {
  it('offset 0 defers to the API months path', () => {
    expect(periodWindow({ kind: 'rolling', months: 6 }, '2026-09-15', 0)).toBeNull()
  })

  it('steps back by whole windows that touch but never overlap', () => {
    // 6M on 2026-09-15 covers 2026-03-01 to today: seven calendar months.
    expect(periodWindow({ kind: 'rolling', months: 6 }, '2026-09-15', -1)).toEqual({
      from: '2025-08-01',
      to: '2026-02-28',
    })
    expect(periodWindow({ kind: 'rolling', months: 6 }, '2026-09-15', -2)).toEqual({
      from: '2025-01-01',
      to: '2025-07-31',
    })
    // 1Y on 2026-09-15 covers 2025-09-01 to today: thirteen calendar months.
    expect(periodWindow({ kind: 'rolling', months: 12 }, '2026-09-15', -1)).toEqual({
      from: '2024-08-01',
      to: '2025-08-31',
    })
  })

  it('ends the previous window the day before the current one starts', () => {
    const today = '2026-10-23'
    for (const months of [3, 6, 12, 24]) {
      const previous = periodWindow({ kind: 'rolling', months }, today, -1)
      expect(previous && addDaysIso(previous.to, 1)).toBe(rollingStart(today, months))
    }
  })
})

describe('periodWindow: days presets', () => {
  it('offset 0 defers to the API days path', () => {
    expect(periodWindow({ kind: 'days', days: 30 }, '2026-09-15', 0)).toBeNull()
  })

  it('steps the rolling window back by its own length', () => {
    expect(periodWindow({ kind: 'days', days: 30 }, '2026-09-15', -1)).toEqual({
      from: '2026-07-18',
      to: '2026-08-16',
    })
  })
})

describe('periodWindow: custom ranges', () => {
  const range = { from: '2026-05-10', to: '2026-08-20' }

  it('offset 0 defers to the page-sent dates', () => {
    expect(periodWindow({ kind: 'custom' }, '2026-09-15', 0, range)).toBeNull()
  })

  it('steps the picked range back by its own length in months', () => {
    expect(periodWindow({ kind: 'custom' }, '2026-09-15', -1, range)).toEqual({
      from: '2026-01-10',
      to: '2026-04-20',
    })
  })

  it('steps a picked year back one year, not thirteen months', () => {
    const year = { from: '2025-01-01', to: '2025-12-31' }
    expect(periodWindow({ kind: 'custom' }, '2026-09-15', -1, year)).toEqual({
      from: '2024-01-01',
      to: '2024-12-31',
    })
  })

  it('steps a picked year forward again to where it started', () => {
    const year = { from: '2025-01-01', to: '2025-12-31' }
    expect(periodWindow({ kind: 'custom' }, '2026-09-15', 1, year)).toEqual({
      from: '2026-01-01',
      to: '2026-12-31',
    })
  })

  it('keeps February whole when a leap year steps back', () => {
    const window = { from: '2024-01-01', to: '2024-02-29' }
    expect(periodWindow({ kind: 'custom' }, '2026-09-15', -1, window)).toEqual({
      from: '2023-11-01',
      to: '2023-12-31',
    })
  })

  it('steps a half-year of whole months back by six months', () => {
    const half = { from: '2026-07-01', to: '2026-12-31' }
    expect(periodWindow({ kind: 'custom' }, '2026-09-15', -1, half)).toEqual({
      from: '2026-01-01',
      to: '2026-06-30',
    })
  })
})

describe('alignedSpanMonths', () => {
  it('counts the months a range of whole months covers', () => {
    expect(alignedSpanMonths({ from: '2025-01-01', to: '2025-12-31' })).toBe(12)
    expect(alignedSpanMonths({ from: '2026-07-01', to: '2026-12-31' })).toBe(6)
    expect(alignedSpanMonths({ from: '2026-03-01', to: '2026-03-31' })).toBe(1)
    expect(alignedSpanMonths({ from: '2024-02-01', to: '2024-02-29' })).toBe(1)
  })

  it('declines a range that does not start on the first', () => {
    expect(alignedSpanMonths({ from: '2026-05-10', to: '2026-08-31' })).toBeNull()
  })

  it('declines a range that stops short of the month end', () => {
    expect(alignedSpanMonths({ from: '2026-01-01', to: '2026-10-26' })).toBeNull()
  })
})
