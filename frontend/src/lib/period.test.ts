import { describe, expect, it } from 'vitest'

import {
  resolvePeriod,
  shiftAnchor,
  weekStartFromLocale,
  type PeriodMode,
} from './period'

// Mirror of backend test_period_service.py — same anchors, same expected
// ranges. Keep them in lockstep.

describe('resolvePeriod', () => {
  it('daily returns the anchor as both start and end', () => {
    expect(resolvePeriod('daily', '2026-06-15')).toEqual({
      start: '2026-06-15',
      end: '2026-06-15',
    })
  })

  describe('weekly', () => {
    it('Sunday-start covers Sun..Sat when anchor is Wed', () => {
      expect(resolvePeriod('weekly', '2026-06-17', 0)).toEqual({
        start: '2026-06-14', // Sunday
        end: '2026-06-20', // Saturday
      })
    })

    it('Monday-start covers Mon..Sun when anchor is Wed', () => {
      expect(resolvePeriod('weekly', '2026-06-17', 1)).toEqual({
        start: '2026-06-15', // Monday
        end: '2026-06-21', // Sunday
      })
    })

    it('Monday-start: anchor Sunday falls into the previous Mon-week', () => {
      expect(resolvePeriod('weekly', '2026-06-14', 1)).toEqual({
        start: '2026-06-08',
        end: '2026-06-14',
      })
    })

    it('crosses year boundary correctly', () => {
      expect(resolvePeriod('weekly', '2027-01-02', 0)).toEqual({
        start: '2026-12-27',
        end: '2027-01-02',
      })
    })
  })

  describe('monthly', () => {
    const cases: Array<[string, string, string]> = [
      ['2026-06-15', '2026-06-01', '2026-06-30'],
      ['2026-04-01', '2026-04-01', '2026-04-30'],
      ['2026-02-10', '2026-02-01', '2026-02-28'], // non-leap
      ['2024-02-10', '2024-02-01', '2024-02-29'], // leap
      ['2026-12-31', '2026-12-01', '2026-12-31'],
      ['2026-01-01', '2026-01-01', '2026-01-31'],
    ]
    for (const [anchor, start, end] of cases) {
      it(`anchor=${anchor} → ${start}..${end}`, () => {
        expect(resolvePeriod('monthly', anchor)).toEqual({ start, end })
      })
    }
  })

  describe('quarterly', () => {
    const cases: Array<[string, string, string]> = [
      ['2026-02-15', '2026-01-01', '2026-03-31'], // Q1
      ['2026-05-15', '2026-04-01', '2026-06-30'], // Q2
      ['2026-08-15', '2026-07-01', '2026-09-30'], // Q3
      ['2026-11-15', '2026-10-01', '2026-12-31'], // Q4
      ['2026-03-31', '2026-01-01', '2026-03-31'], // last day of Q1
    ]
    for (const [anchor, start, end] of cases) {
      it(`anchor=${anchor} → ${start}..${end}`, () => {
        expect(resolvePeriod('quarterly', anchor)).toEqual({ start, end })
      })
    }
  })

  describe('half_yearly', () => {
    const cases: Array<[string, string, string]> = [
      ['2026-01-15', '2026-01-01', '2026-06-30'], // S1
      ['2026-06-30', '2026-01-01', '2026-06-30'], // S1 last day
      ['2026-07-01', '2026-07-01', '2026-12-31'], // S2 first day
      ['2026-09-15', '2026-07-01', '2026-12-31'], // S2
      ['2026-12-31', '2026-07-01', '2026-12-31'], // S2 last day
    ]
    for (const [anchor, start, end] of cases) {
      it(`anchor=${anchor} → ${start}..${end}`, () => {
        expect(resolvePeriod('half_yearly', anchor)).toEqual({ start, end })
      })
    }
  })

  it('yearly covers Jan 1..Dec 31', () => {
    expect(resolvePeriod('yearly', '2026-06-15')).toEqual({
      start: '2026-01-01',
      end: '2026-12-31',
    })
  })

  it('custom throws (caller must supply from/to)', () => {
    expect(() => resolvePeriod('custom', '2026-06-15')).toThrow(/custom mode/)
  })
})

describe('shiftAnchor', () => {
  it('daily steps by 1 day', () => {
    expect(shiftAnchor('daily', '2026-06-15', 1)).toBe('2026-06-16')
    expect(shiftAnchor('daily', '2026-06-15', -1)).toBe('2026-06-14')
    expect(shiftAnchor('daily', '2026-12-31', 1)).toBe('2027-01-01')
  })

  it('weekly steps by 7 days', () => {
    expect(shiftAnchor('weekly', '2026-06-15', 1)).toBe('2026-06-22')
    expect(shiftAnchor('weekly', '2026-06-15', -1)).toBe('2026-06-08')
  })

  describe('monthly handles day overflow', () => {
    const cases: Array<[string, 1 | -1, string]> = [
      ['2026-01-31', 1, '2026-02-28'], // non-leap
      ['2024-01-31', 1, '2024-02-29'], // leap
      ['2026-03-31', -1, '2026-02-28'],
      ['2026-12-15', 1, '2027-01-15'],
      ['2026-01-15', -1, '2025-12-15'],
      ['2026-01-31', 1, '2026-02-28'], // clamp day=31 to 28
    ]
    for (const [anchor, dir, expected] of cases) {
      it(`${anchor} ${dir > 0 ? '+' : '-'}1 month → ${expected}`, () => {
        expect(shiftAnchor('monthly', anchor, dir)).toBe(expected)
      })
    }
  })

  it('quarterly steps by 3 months', () => {
    expect(shiftAnchor('quarterly', '2026-02-15', 1)).toBe('2026-05-15')
    expect(shiftAnchor('quarterly', '2026-05-15', -1)).toBe('2026-02-15')
    expect(shiftAnchor('quarterly', '2026-11-15', 1)).toBe('2027-02-15')
  })

  it('half_yearly steps by 6 months', () => {
    expect(shiftAnchor('half_yearly', '2026-03-15', 1)).toBe('2026-09-15')
    expect(shiftAnchor('half_yearly', '2026-09-15', -1)).toBe('2026-03-15')
  })

  it('yearly steps by 1 year (handles Feb 29 → Feb 28 on non-leap)', () => {
    expect(shiftAnchor('yearly', '2024-02-29', 1)).toBe('2025-02-28')
    expect(shiftAnchor('yearly', '2024-06-15', 1)).toBe('2025-06-15')
    expect(shiftAnchor('yearly', '2026-06-15', -1)).toBe('2025-06-15')
  })

  it('custom mode throws', () => {
    expect(() => shiftAnchor('custom', '2026-06-15', 1)).toThrow(/custom/)
  })
})

describe('shiftAnchor + resolvePeriod round-trip', () => {
  const modes: PeriodMode[] = ['monthly', 'quarterly', 'half_yearly', 'yearly']
  for (const mode of modes) {
    it(`${mode}: shift +1 then resolve yields the next period`, () => {
      const a = '2026-06-15'
      const next = shiftAnchor(mode, a, 1)
      // Pick any anchor inside the new period — first day works for monthly/quarterly/half_yearly/yearly.
      const aRange = resolvePeriod(mode, a)
      const nextRange = resolvePeriod(mode, next)
      expect(aRange.end < nextRange.start).toBe(true)
    })
  }
})

describe('weekStartFromLocale', () => {
  // Values come from CLDR via Intl.Locale.weekInfo (Node 22+). The static
  // table inside the function is a defense-in-depth fallback for environments
  // where Intl is incomplete; in practice the Intl branch always wins for
  // well-formed locale strings. Country tags matter — es is Monday-start but
  // es-MX is Sunday-start, so we test the exact tags Securo actually exposes.

  // Sunday-start locales (per CLDR via Intl)
  it.each([
    'pt-BR', 'pt-PT', 'en-US', 'es-MX', 'ja-JP', 'ko-KR', 'he-IL',
  ])('%s → 0 (Sunday)', (locale) => {
    expect(weekStartFromLocale(locale)).toBe(0)
  })

  // Monday-start locales (per CLDR via Intl)
  it.each([
    'en-GB', 'es', 'es-AR', 'zh-CN',
    'fr-FR', 'de-DE', 'it-IT', 'nl-NL', 'pl-PL',
    'ru-RU', 'uk-UA', 'sv-SE', 'da-DK', 'no-NO', 'fi-FI',
  ])('%s → 1 (Monday)', (locale) => {
    expect(weekStartFromLocale(locale)).toBe(1)
  })

  // Intl is lenient — it accepts strings the runtime doesn't recognize and
  // falls back to a default firstDay. We trust Intl in those cases rather
  // than maintaining a parallel locale registry.
  it('Intl default for xx-XX is Monday (1)', () => {
    expect(weekStartFromLocale('xx-XX')).toBe(1)
  })

  it('Intl default for an unrecognized tag is Sunday (0)', () => {
    expect(weekStartFromLocale('not-a-locale')).toBe(0)
  })

  it('falls back to the static table when Intl throws (empty string)', () => {
    // Empty string is rejected by new Intl.Locale, so the catch branch runs
    // and the static table check kicks in. "" isn't in SUNDAY_LOCALES, so
    // we land on the Monday default.
    expect(weekStartFromLocale('')).toBe(1)
  })
})