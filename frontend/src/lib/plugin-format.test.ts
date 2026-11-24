import { describe, expect, it } from 'vitest'

import { intervalLabel } from './plugin-format'

describe('intervalLabel', () => {
  it('names a schedule in the largest whole unit, in the UI language', () => {
    expect(intervalLabel(15, 'en')).toBe('15 minutes')
    expect(intervalLabel(60, 'en')).toBe('1 hour')
    expect(intervalLabel(90, 'en')).toBe('90 minutes')
    expect(intervalLabel(10080, 'en')).toBe('7 days')
    expect(intervalLabel(120, 'pt-BR')).toBe('2 horas')
  })
})
