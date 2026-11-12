import { applyThemeVars, DEFAULT_THEME_ID, findTheme, themeVars } from '@/lib/themes'

/**
 * Securo's own theme with the instance's accent colors, for pages shown
 * before anyone signs in (login, register). Inside the app, `useAppTheme`
 * keeps the theme each person picked instead.
 */
export function setThemeBasedOnSystem(lightColor: string | null, darkColor: string | null, resolvedTheme?: string) {
  const mode = resolvedTheme === 'dark' ? 'dark' : 'light'
  applyThemeVars(themeVars(findTheme(DEFAULT_THEME_ID), mode, { light: lightColor, dark: darkColor }))
}
