import { describe, expect, it } from 'vitest'
import { addMonths, flagDuePresets, flagFilterToParam, flagState, parseFlagFilter } from '@/lib/transaction-flag'

describe('flagState', () => {
  const today = '2026-10-20'

  it('reads an unflagged row as none, whatever its date', () => {
    expect(flagState({ is_flagged: false, flag_due_date: '2026-01-01' }, today)).toBe('none')
  })

  it('reads a flag with no date as plain flagged', () => {
    expect(flagState({ is_flagged: true, flag_due_date: null }, today)).toBe('flagged')
  })

  it('is overdue from the day after its date', () => {
    expect(flagState({ is_flagged: true, flag_due_date: '2026-10-19' }, today)).toBe('overdue')
    expect(flagState({ is_flagged: true, flag_due_date: '2026-10-20' }, today)).toBe('soon')
  })

  it('is due soon up to a week ahead, matching the server', () => {
    expect(flagState({ is_flagged: true, flag_due_date: '2026-10-27' }, today)).toBe('soon')
    expect(flagState({ is_flagged: true, flag_due_date: '2026-10-28' }, today)).toBe('flagged')
  })
})

describe('flag reminder presets', () => {
  it('offers tomorrow, a week and a month from today', () => {
    expect(flagDuePresets('2026-10-20').map((p) => p.date)).toEqual(['2026-10-21', '2026-10-27', '2026-11-20'])
  })

  it('keeps a month ahead inside a shorter month', () => {
    expect(addMonths('2026-01-31', 1)).toBe('2026-02-28')
  })
})

describe('flag filter in the URL', () => {
  it('round-trips each view and ignores anything else', () => {
    for (const filter of ['all', 'overdue', 'soon', ''] as const) {
      expect(parseFlagFilter(flagFilterToParam(filter) || null)).toBe(filter)
    }
    expect(parseFlagFilter('nonsense')).toBe('')
  })
})
