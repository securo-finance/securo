import { describe, expect, it } from 'vitest'
import { nextTheme } from './theme-cycle'

describe('nextTheme', () => {
  it('steps light → dark → system → light', () => {
    expect(nextTheme('light')).toBe('dark')
    expect(nextTheme('dark')).toBe('system')
    expect(nextTheme('system')).toBe('light')
  })

  it('treats an unknown or missing theme as the dark default', () => {
    expect(nextTheme(undefined)).toBe('system')
    expect(nextTheme('sepia')).toBe('system')
    expect(nextTheme('__proto__')).toBe('system')
    expect(nextTheme('toString')).toBe('system')
  })
})
