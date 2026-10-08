import { describe, expect, it } from 'vitest'

import {
  FREQUENCIES,
  PERIODS_PER_YEAR,
  endPayload,
  isUpcomingTerm,
  localToday,
  monthlyEquivalent,
  periodLabel,
  scheduleActions,
  scheduleLabel,
  scheduleTone,
} from './invoice-schedule-utils'

describe('monthlyEquivalent', () => {
  it('normalises through the year, not through a four-week month', () => {
    expect(monthlyEquivalent(1200, 'weekly')).toBe(5200)
    expect(monthlyEquivalent(1200, 'biweekly')).toBe(2600)
    expect(monthlyEquivalent(1200, 'monthly')).toBe(1200)
    expect(monthlyEquivalent(1200, 'quarterly')).toBe(400)
    expect(monthlyEquivalent(1200, 'semiannual')).toBe(200)
    expect(monthlyEquivalent(1200, 'yearly')).toBe(100)
  })

  it('rounds to cents', () => {
    expect(monthlyEquivalent(100, 'quarterly')).toBe(33.33)
  })

  it('covers every frequency the picker offers', () => {
    for (const frequency of FREQUENCIES) expect(PERIODS_PER_YEAR[frequency]).toBeGreaterThan(0)
  })
})

describe('scheduleLabel and scheduleTone', () => {
  it('outranks "active" with what needs a person', () => {
    expect(scheduleLabel({ status: 'active', pause_reason: null, past_due_count: 0 })).toBe('active')
    expect(scheduleLabel({ status: 'active', pause_reason: null, past_due_count: 2 })).toBe('past_due')
    expect(scheduleLabel({ status: 'paused', pause_reason: 'manual', past_due_count: 0 })).toBe('paused')
    expect(scheduleLabel({ status: 'paused', pause_reason: 'failures', past_due_count: 0 })).toBe('paused_failures')
    expect(scheduleLabel({ status: 'ended', pause_reason: null, past_due_count: 3 })).toBe('ended')
  })

  it('is loud only for a pause after failures and a late invoice', () => {
    expect(scheduleTone({ status: 'paused', pause_reason: 'failures', past_due_count: 0 })).toContain('rose')
    expect(scheduleTone({ status: 'active', pause_reason: null, past_due_count: 1 })).toContain('amber')
    expect(scheduleTone({ status: 'active', pause_reason: null, past_due_count: 0 })).toContain('emerald')
    expect(scheduleTone({ status: 'ended', pause_reason: null, past_due_count: 0 })).toContain('muted')
  })
})

describe('scheduleActions', () => {
  it('follows the stored decisions', () => {
    const active = scheduleActions({ status: 'active', invoice_count: 0, origin: 'local' })
    expect(active).toMatchObject({ canPause: true, canResume: false, canEnd: true, canGenerate: true, canDelete: true })

    const billed = scheduleActions({ status: 'active', invoice_count: 3, origin: 'local' })
    expect(billed.canDelete).toBe(false)

    const paused = scheduleActions({ status: 'paused', invoice_count: 3, origin: 'local' })
    expect(paused).toMatchObject({ canPause: false, canResume: true, canGenerate: false, canChangePrice: true })

    const ended = scheduleActions({ status: 'ended', invoice_count: 3, origin: 'local' })
    expect(ended).toMatchObject({ canPause: false, canResume: false, canEnd: false, canEdit: false, canChangePrice: false })
  })

  it('never generates for a mirrored gateway agreement', () => {
    expect(scheduleActions({ status: 'active', invoice_count: 0, origin: 'imported' }).canGenerate).toBe(false)
  })
})

describe('isUpcomingTerm', () => {
  it('is strictly after today', () => {
    const today = new Date(2026, 8, 22)
    expect(isUpcomingTerm('2026-09-23', today)).toBe(true)
    expect(isUpcomingTerm('2026-09-22', today)).toBe(false)
    expect(isUpcomingTerm('2026-09-21', today)).toBe(false)
  })
})

describe('periodLabel', () => {
  it('writes a span the way the locale does, dropping the repeated year', () => {
    const label = periodLabel('2026-09-05', '2026-10-04', 'en-US')
    expect(label).toContain('Sep 5')
    expect(label).toContain('Oct 4, 2026')
    expect(label.match(/2026/g)).toHaveLength(1)
  })

  it('keeps both years when the period crosses one', () => {
    const label = periodLabel('2026-12-05', '2027-01-04', 'en-US')
    expect(label).toContain('2026')
    expect(label).toContain('2027')
  })
})

describe('endPayload', () => {
  it('sends only the field the chosen shape needs, and nulls the other', () => {
    expect(endPayload('never', '2027-01-01', '5')).toEqual({ end_type: 'never', end_date: null, end_count: null })
    expect(endPayload('on_date', '2027-01-01', '5')).toEqual({ end_type: 'on_date', end_date: '2027-01-01', end_count: null })
    expect(endPayload('after_count', '2027-01-01', '5')).toEqual({ end_type: 'after_count', end_date: null, end_count: 5 })
  })
})

describe('localToday', () => {
  it('is the local calendar date, not the UTC one', () => {
    // 23:30 on Sep 22 in local time. In UTC west of Greenwich this is
    // already Sep 23, which is what `toISOString()` would have said.
    expect(localToday(new Date(2026, 8, 22, 23, 30))).toBe('2026-09-22')
    expect(localToday(new Date(2026, 0, 5, 0, 5))).toBe('2026-01-05')
  })
})
