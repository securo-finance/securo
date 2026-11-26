// "System" follows the OS colour scheme and switches with it (issue #1203).
export const THEME_CHOICES = ['light', 'dark', 'system'] as const
export type ThemeChoice = (typeof THEME_CHOICES)[number]

/** The saved theme, or dark (Securo's default) when it is missing or unknown. */
export function themeChoice(theme: string | undefined): ThemeChoice {
  return THEME_CHOICES.find((choice) => choice === theme) ?? 'dark'
}

export const THEME_LABEL_KEY: Record<ThemeChoice, string> = {
  light: 'settings.themeLight',
  dark: 'settings.themeDark',
  system: 'settings.themeSystem',
}
