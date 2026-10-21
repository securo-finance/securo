// The header's theme button steps Light → Dark → System → Light (issue
// #1203). "System" follows the OS colour scheme and switches with it.
export type ThemeChoice = 'light' | 'dark' | 'system'

const NEXT: Record<ThemeChoice, ThemeChoice> = {
  light: 'dark',
  dark: 'system',
  system: 'light',
}

export function nextTheme(theme: string | undefined): ThemeChoice {
  return NEXT[(theme as ThemeChoice) in NEXT ? (theme as ThemeChoice) : 'dark']
}

export const THEME_LABEL_KEY: Record<ThemeChoice, string> = {
  light: 'settings.themeLight',
  dark: 'settings.themeDark',
  system: 'settings.themeSystem',
}
