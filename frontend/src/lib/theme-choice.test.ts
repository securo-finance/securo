import { describe, expect, it } from 'vitest'
import { themeChoice } from './theme-choice'

describe('themeChoice', () => {
  it('keeps a known theme', () => {
    expect(themeChoice('light')).toBe('light')
    expect(themeChoice('system')).toBe('system')
  })

  it('treats an unknown or missing theme as the dark default', () => {
    expect(themeChoice(undefined)).toBe('dark')
    expect(themeChoice('sepia')).toBe('dark')
    expect(themeChoice('__proto__')).toBe('dark')
  })
})
