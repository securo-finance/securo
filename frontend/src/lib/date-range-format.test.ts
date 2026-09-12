import { describe, expect, it } from 'vitest'

import { formatDateRange } from './date-range-format'

describe('formatDateRange', () => {
  it('formats a closed range with the year by default', () => {
    expect(formatDateRange('2025-09-26', '2026-01-30', 'en-US')).toBe('Sep 26, 2025 — Jan 30, 2026')
  })

  it('drops the year in compact mode, even across a year boundary', () => {
    expect(formatDateRange('2025-09-26', '2026-01-30', 'en-US', { compact: true })).toBe(
      'Sep 26 — Jan 30',
    )
  })

  it('formats a from-only lower bound', () => {
    expect(formatDateRange('2026-03-01', '', 'en-US')).toBe('≥ Mar 01, 2026')
    expect(formatDateRange('2026-03-01', '', 'en-US', { compact: true })).toBe('≥ Mar 01')
  })

  it('formats a to-only upper bound', () => {
    expect(formatDateRange('', '2026-03-10', 'en-US')).toBe('≤ Mar 10, 2026')
    expect(formatDateRange('', '2026-03-10', 'en-US', { compact: true })).toBe('≤ Mar 10')
  })

  it('localizes month names for the requested locale', () => {
    expect(formatDateRange('2026-03-01', '2026-03-10', 'es-ES')).toMatch(/mar\.?/i)
  })
})
