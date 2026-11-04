import { afterEach, describe, expect, it } from 'vitest'

import { setThemeBasedOnSystem } from '@/lib/theme-utils'

const VARS = [
  '--primary',
  '--ring',
  '--sidebar-primary',
  '--accent',
  '--accent-foreground',
  '--muted',
  '--sidebar-accent',
  '--sidebar-accent-foreground',
]

function readVar(name: string): string {
  return document.documentElement.style.getPropertyValue(name)
}

afterEach(() => {
  for (const name of VARS) {
    document.documentElement.style.removeProperty(name)
  }
})

describe('setThemeBasedOnSystem', () => {
  it('applies the light colour on a light theme', () => {
    setThemeBasedOnSystem('#ff0000', '#0000ff', 'light')

    expect(readVar('--primary')).toBe('#ff0000')
    expect(readVar('--ring')).toBe('#ff0000')
    expect(readVar('--sidebar-primary')).toBe('#ff0000')
  })

  it('applies the dark colour on a dark theme', () => {
    setThemeBasedOnSystem('#ff0000', '#0000ff', 'dark')

    expect(readVar('--primary')).toBe('#0000ff')
  })

  it('treats an unknown theme as light', () => {
    setThemeBasedOnSystem('#ff0000', '#0000ff', undefined)

    expect(readVar('--primary')).toBe('#ff0000')
  })

  it('mixes toward black on dark and toward white on light', () => {
    // Getting this backwards produces an accent that vanishes into the
    // background it sits on.
    setThemeBasedOnSystem('#ff0000', '#0000ff', 'dark')
    expect(readVar('--accent')).toContain('black')
    expect(readVar('--accent-foreground')).toContain('white')

    setThemeBasedOnSystem('#ff0000', '#0000ff', 'light')
    expect(readVar('--accent')).toContain('white')
    expect(readVar('--accent-foreground')).toContain('black')
  })

  it('derives every accent variable from the chosen colour', () => {
    setThemeBasedOnSystem('#ff0000', '#0000ff', 'light')

    for (const name of VARS) {
      expect(readVar(name), name).not.toBe('')
    }
  })

  it('clears the overrides when the deployment sets no colour', () => {
    // Leaving stale properties behind would pin a previous admin's brand
    // colour after they cleared it.
    setThemeBasedOnSystem('#ff0000', '#0000ff', 'light')
    setThemeBasedOnSystem(null, null, 'light')

    for (const name of VARS) {
      expect(readVar(name), name).toBe('')
    }
  })

  it('clears when only the colour for the active theme is missing', () => {
    setThemeBasedOnSystem('#ff0000', '#0000ff', 'light')
    setThemeBasedOnSystem(null, '#0000ff', 'light')

    expect(readVar('--primary')).toBe('')
  })
})
