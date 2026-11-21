// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'

import {
  applyThemeVars,
  BUILT_IN_THEMES,
  contrastRatio,
  DEFAULT_THEME_ID,
  findTheme,
  READABLE_PAIRS,
  THEME_TOKENS,
  themeVars,
} from './themes'

const HEX = /^#[0-9A-F]{6}$/i

describe('built-in themes', () => {
  it('have unique ids, Securo first', () => {
    const ids = BUILT_IN_THEMES.map((theme) => theme.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids[0]).toBe(DEFAULT_THEME_ID)
  })

  for (const theme of BUILT_IN_THEMES.filter((t) => t.id !== DEFAULT_THEME_ID)) {
    for (const mode of ['light', 'dark'] as const) {
      it(`${theme.id} (${mode}) keeps text readable`, () => {
        const colors = theme[mode]
        for (const [text, background] of READABLE_PAIRS) {
          expect(colors[text], `${text}`).toMatch(HEX)
          expect(colors[background], `${background}`).toMatch(HEX)
          expect(contrastRatio(colors[text]!, colors[background]!), `${text} on ${background}`).toBeGreaterThanOrEqual(4.5)
        }
      })

      it(`${theme.id} (${mode}) only sets known tokens, as hex`, () => {
        for (const [token, value] of Object.entries(theme[mode])) {
          expect(THEME_TOKENS).toContain(token)
          expect(value).toMatch(HEX)
        }
      })
    }
  }
})

describe('themeVars', () => {
  const ocean = findTheme('ocean')

  it('fills in the tokens that follow another', () => {
    const vars = themeVars(ocean, 'light')
    expect(vars['--card']).toBe('#FFFFFF')
    expect(vars['--popover']).toBe('#FFFFFF')
    expect(vars['--sidebar']).toBe('#FFFFFF')
    expect(vars['--input']).toBe(ocean.light.border)
    expect(vars['--ring']).toBe(ocean.light.primary)
    expect(vars['--accent']).toContain(ocean.light.primary)
  })

  it('switches with the mode', () => {
    expect(themeVars(ocean, 'dark')['--background']).toBe(ocean.dark.background)
  })

  it("leaves Securo's own look alone without an accent", () => {
    expect(themeVars(findTheme(DEFAULT_THEME_ID), 'light')).toEqual({})
  })

  it("colors only Securo's own look with the instance accent", () => {
    const accent = { light: '#FF0000', dark: '#00FF00' }
    const own = themeVars(findTheme(DEFAULT_THEME_ID), 'dark', accent)
    expect(own['--primary']).toBe('#00FF00')
    expect(own['--muted']).toContain('#00FF00')
    expect(themeVars(ocean, 'dark', accent)['--primary']).toBe(ocean.dark.primary)
  })

  it('sets the radius only when the theme has one', () => {
    expect(themeVars(findTheme('graphite'), 'light')['--radius']).toBe('0.5rem')
    expect(themeVars(ocean, 'light')['--radius']).toBeUndefined()
  })
})

describe('findTheme', () => {
  it("falls back to Securo's own look for an id it doesn't know", () => {
    expect(findTheme('plugin:gone/away:theme').id).toBe(DEFAULT_THEME_ID)
    expect(findTheme(null).id).toBe(DEFAULT_THEME_ID)
  })
})

describe('applyThemeVars', () => {
  it('clears what the previous theme set', () => {
    const root = document.createElement('div')
    applyThemeVars({ '--primary': '#111111', '--radius': '0.5rem' }, root)
    applyThemeVars({ '--primary': '#222222' }, root)
    expect(root.style.getPropertyValue('--primary')).toBe('#222222')
    expect(root.style.getPropertyValue('--radius')).toBe('')
  })
})

describe('contrastRatio', () => {
  it('matches the WCAG extremes', () => {
    expect(contrastRatio('#000000', '#FFFFFF')).toBeCloseTo(21, 0)
    expect(contrastRatio('#777777', '#777777')).toBe(1)
  })
})
