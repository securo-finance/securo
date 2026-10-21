// The header's theme button steps Light → Dark → System → Light (issue
// #1203). "System" follows the OS colour scheme and switches with it.
export type ThemeChoice = 'light' | 'dark' | 'system'

const NEXT: Record<ThemeChoice, ThemeChoice> = {
  light: 'dark',
  dark: 'system',
  system: 'light',
}

function isThemeChoice(theme: string | undefined): theme is ThemeChoice {
  return theme !== undefined && Object.hasOwn(NEXT, theme)
}

export function nextTheme(theme: string | undefined): ThemeChoice {
  return NEXT[isThemeChoice(theme) ? theme : 'dark']
}

export const THEME_LABEL_KEY: Record<ThemeChoice, string> = {
  light: 'settings.themeLight',
  dark: 'settings.themeDark',
  system: 'settings.themeSystem',
}
