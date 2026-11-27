/**
 * Themes: a look for Securo as values for its design tokens, in light and
 * dark. Nothing else: no CSS, no fonts, no images. A theme only fills in the
 * tokens it names; the rest keep Securo's own values from index.css.
 *
 * The built-in themes below use the same shape a plugin's will, so the
 * library treats them alike.
 */

export type ThemeMode = 'light' | 'dark'

/** Tokens a theme may set, each a `#RRGGBB` color. */
export const THEME_TOKENS = [
  'background',
  'foreground',
  'card',
  'primary',
  'primary-foreground',
  'muted',
  'muted-foreground',
  'border',
  'chart-1',
  'chart-2',
  'chart-3',
  'chart-4',
  'chart-5',
  'chart-6',
  'chart-7',
  'chart-8',
  'chart-9',
  'chart-10',
  'chart-11',
  'chart-12',
  'chart-muted',
  'chart-income',
  'chart-expense',
] as const

export type ThemeToken = (typeof THEME_TOKENS)[number]
export type ThemeColors = Partial<Record<ThemeToken, string>>

/** Font families a theme may pick: the device's own, nothing is downloaded. */
export type ThemeFont = 'sans' | 'mono' | 'serif'

const FONT_STACKS: Record<Exclude<ThemeFont, 'sans'>, string> = {
  mono: 'ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Code", Menlo, Consolas, "Liberation Mono", monospace',
  serif: 'ui-serif, "Iowan Old Style", "Palatino Linotype", Georgia, Cambria, serif',
}

export interface Theme {
  id: string
  light: ThemeColors
  dark: ThemeColors
  /** Corner radius in rem, 0 to 1. Securo's own is 0.625. */
  radius?: number
  /** The family text is set in; Securo's own is its sans. */
  font?: ThemeFont
  /** For a plugin's theme: its name, and the plugin's. Built-in themes are named by id. */
  name?: Record<string, string>
  plugin?: { id: string; name: Record<string, string> }
}

/** Securo's own look: what index.css defines, plus the instance's accent colors. */
export const DEFAULT_THEME_ID = 'securo'

export const BUILT_IN_THEMES: Theme[] = [
  { id: DEFAULT_THEME_ID, light: {}, dark: {} },
  {
    id: 'ocean',
    light: {
      background: '#F4F8FA',
      foreground: '#0B1F2A',
      card: '#FFFFFF',
      primary: '#0E7490',
      'primary-foreground': '#FFFFFF',
      muted: '#E8F1F5',
      'muted-foreground': '#4B6573',
      border: '#DCE7EC',
      'chart-1': '#0891B2',
    },
    dark: {
      background: '#071117',
      foreground: '#E6F1F5',
      card: '#0E1C24',
      primary: '#22D3EE',
      'primary-foreground': '#062C36',
      muted: '#15272F',
      'muted-foreground': '#8AA4B0',
      border: '#1F333C',
      'chart-1': '#22D3EE',
    },
  },
  {
    id: 'forest',
    light: {
      background: '#F6F8F5',
      foreground: '#13201A',
      card: '#FFFFFF',
      primary: '#15803D',
      'primary-foreground': '#FFFFFF',
      muted: '#EBF1EA',
      'muted-foreground': '#536257',
      border: '#DFE6DD',
      'chart-1': '#16A34A',
    },
    dark: {
      background: '#0A0F0C',
      foreground: '#E8F0EA',
      card: '#121A15',
      primary: '#4ADE80',
      'primary-foreground': '#052E16',
      muted: '#1B251E',
      'muted-foreground': '#8FA195',
      border: '#24312A',
      'chart-1': '#4ADE80',
    },
  },
  {
    id: 'sunset',
    light: {
      background: '#FBF8F6',
      foreground: '#1F1410',
      card: '#FFFFFF',
      primary: '#C2410C',
      'primary-foreground': '#FFFFFF',
      muted: '#F5EEE9',
      'muted-foreground': '#6B5A50',
      border: '#EDE3DC',
      'chart-1': '#EA580C',
    },
    dark: {
      background: '#110C0A',
      foreground: '#F5EDE8',
      card: '#1A1311',
      primary: '#FB923C',
      'primary-foreground': '#431407',
      muted: '#261C18',
      'muted-foreground': '#A8968C',
      border: '#33261F',
      'chart-1': '#FB923C',
    },
  },
  {
    id: 'graphite',
    radius: 0.5,
    light: {
      background: '#FAFAFA',
      foreground: '#09090B',
      card: '#FFFFFF',
      primary: '#18181B',
      'primary-foreground': '#FAFAFA',
      muted: '#F4F4F5',
      'muted-foreground': '#63636B',
      border: '#E4E4E7',
      'chart-1': '#52525B',
    },
    dark: {
      background: '#09090B',
      foreground: '#FAFAFA',
      card: '#141416',
      primary: '#FAFAFA',
      'primary-foreground': '#18181B',
      muted: '#232326',
      'muted-foreground': '#A1A1AA',
      border: '#2A2A2E',
      'chart-1': '#D4D4D8',
    },
  },
  {
    // Dark first: an electric blue on deep navy.
    id: 'midnight',
    light: {
      background: '#F4F7FC',
      foreground: '#0A1222',
      card: '#FFFFFF',
      primary: '#1D4ED8',
      'primary-foreground': '#FFFFFF',
      muted: '#E8EEF8',
      'muted-foreground': '#4A5873',
      border: '#DAE2EF',
      'chart-1': '#2563EB',
      'chart-2': '#06B6D4',
      'chart-3': '#8B5CF6',
    },
    dark: {
      background: '#060A13',
      foreground: '#E6EDF8',
      card: '#0C1322',
      primary: '#4F8CFF',
      'primary-foreground': '#04102A',
      muted: '#141E33',
      'muted-foreground': '#8C9AB5',
      border: '#1B2740',
      'chart-1': '#4F8CFF',
      'chart-2': '#22D3EE',
      'chart-3': '#A78BFA',
      'chart-muted': '#55627A',
    },
  },
  {
    // A terminal: green on near-black, square corners.
    id: 'terminal',
    radius: 0.25,
    light: {
      background: '#F5F8F5',
      foreground: '#0A160D',
      card: '#FFFFFF',
      primary: '#15803D',
      'primary-foreground': '#FFFFFF',
      muted: '#E8F0E9',
      'muted-foreground': '#4E6353',
      border: '#D5E1D7',
      'chart-1': '#16A34A',
      'chart-2': '#0D9488',
      'chart-3': '#65A30D',
    },
    dark: {
      background: '#040705',
      foreground: '#D4F5DB',
      card: '#09100B',
      primary: '#39E079',
      'primary-foreground': '#031408',
      muted: '#122016',
      'muted-foreground': '#7FA488',
      border: '#18291D',
      'chart-1': '#39E079',
      'chart-2': '#2DD4BF',
      'chart-3': '#A3E635',
      'chart-muted': '#4B6352',
    },
  },
  {
    // Violet and magenta, a night-sky palette.
    id: 'nebula',
    light: {
      background: '#FAF7FE',
      foreground: '#1C1030',
      card: '#FFFFFF',
      primary: '#7E22CE',
      'primary-foreground': '#FFFFFF',
      muted: '#F1EAFB',
      'muted-foreground': '#655582',
      border: '#E7DEF5',
      'chart-1': '#9333EA',
      'chart-2': '#DB2777',
      'chart-3': '#6366F1',
    },
    dark: {
      background: '#0A0614',
      foreground: '#F1E9FF',
      card: '#130C22',
      primary: '#C084FC',
      'primary-foreground': '#1E0B33',
      muted: '#1E1433',
      'muted-foreground': '#A595C6',
      border: '#2A1D45',
      'chart-1': '#C084FC',
      'chart-2': '#F472B6',
      'chart-3': '#818CF8',
      'chart-muted': '#5E5179',
    },
  },
  {
    id: 'high-contrast',
    light: {
      background: '#FFFFFF',
      foreground: '#000000',
      card: '#FFFFFF',
      primary: '#1D4ED8',
      'primary-foreground': '#FFFFFF',
      muted: '#F1F1F1',
      'muted-foreground': '#3A3A3A',
      border: '#6B6B6B',
      'chart-1': '#1D4ED8',
    },
    dark: {
      background: '#000000',
      foreground: '#FFFFFF',
      card: '#0A0A0A',
      primary: '#93C5FD',
      'primary-foreground': '#000000',
      muted: '#1F1F1F',
      'muted-foreground': '#D4D4D4',
      border: '#9A9A9A',
      'chart-1': '#93C5FD',
    },
  },
]

/**
 * Securo's own values for the tokens a preview draws, as index.css has them.
 * Only previews read these: the page itself keeps taking them from the CSS,
 * where the instance's accent colors can still change them.
 */
export const SECURO_COLORS: Record<ThemeMode, ThemeColors> = {
  light: {
    background: '#F8F9FB',
    foreground: '#0F172A',
    card: '#FFFFFF',
    primary: '#6366F1',
    'muted-foreground': '#64748B',
    border: '#E8ECF1',
    'chart-1': '#6366F1',
  },
  dark: {
    background: '#0C0D12',
    foreground: '#F0F0F5',
    card: '#16171F',
    primary: '#818CF8',
    'muted-foreground': '#8A8F9E',
    border: '#2A2D3A',
    'chart-1': '#6366F1',
  },
}

export function findTheme(id: string | null | undefined, themes: Theme[] = BUILT_IN_THEMES): Theme {
  return themes.find((theme) => theme.id === id) ?? themes[0]
}

/** The instance's accent colors, set by an admin; they color Securo's own theme only. */
export interface AccentColors {
  light: string | null
  dark: string | null
}

function accentVars(primary: string, mode: ThemeMode, withMuted: boolean): Record<string, string> {
  const mixBase = mode === 'dark' ? 'black' : 'white'
  const contrastBase = mode === 'dark' ? 'white' : 'black'
  const accent = `color-mix(in srgb, ${primary}, ${mixBase} 90%)`
  const accentForeground = `color-mix(in srgb, ${primary}, ${contrastBase} 20%)`
  return {
    '--primary': primary,
    '--ring': primary,
    '--sidebar-primary': primary,
    '--sidebar-ring': primary,
    '--accent': accent,
    '--sidebar-accent': accent,
    '--accent-foreground': accentForeground,
    '--sidebar-accent-foreground': accentForeground,
    ...(withMuted ? { '--muted': `color-mix(in srgb, ${primary}, ${mixBase} 94%)` } : {}),
  }
}

/**
 * The CSS variables a theme sets in a mode, beyond index.css. Tokens that
 * other tokens follow (cards and popovers, the sidebar) are filled in here,
 * so a theme names each color once.
 */
export function themeVars(theme: Theme, mode: ThemeMode, accent?: AccentColors): Record<string, string> {
  const colors = theme[mode]
  const vars: Record<string, string> = {}
  const set = (token: ThemeToken, ...names: string[]) => {
    const value = colors[token]
    if (value) for (const name of names) vars[name] = value
  }
  set('background', '--background')
  set('foreground', '--foreground', '--card-foreground', '--popover-foreground', '--secondary-foreground', '--sidebar-foreground')
  set('card', '--card', '--popover', '--sidebar')
  set('primary-foreground', '--primary-foreground', '--sidebar-primary-foreground')
  set('muted', '--muted', '--secondary')
  set('muted-foreground', '--muted-foreground', '--sidebar-muted')
  set('border', '--border', '--input', '--sidebar-border')
  for (const token of THEME_TOKENS) {
    if (token.startsWith('chart-')) set(token, `--${token}`)
  }
  const primary = colors.primary ?? (theme.id === DEFAULT_THEME_ID ? accent?.[mode] : null)
  if (primary) Object.assign(vars, accentVars(primary, mode, !colors.muted))
  if (theme.radius !== undefined) vars['--radius'] = `${theme.radius}rem`
  if (theme.font && theme.font !== 'sans') vars['--app-font'] = FONT_STACKS[theme.font]
  return vars
}

let applied: string[] = []

/** Sets a theme's variables on the page, clearing whatever the previous one set. */
export function applyThemeVars(vars: Record<string, string>, root: HTMLElement = document.documentElement) {
  for (const name of applied) {
    if (!(name in vars)) root.style.removeProperty(name)
  }
  for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value)
  applied = Object.keys(vars)
}

// --- contrast (WCAG 2.x), used to hold themes to a readable minimum ---

function channel(value: number): number {
  const c = value / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16)
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255)
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** Text pairs a theme must keep readable (WCAG AA, 4.5:1), as [text, background]. */
export const READABLE_PAIRS: [ThemeToken, ThemeToken][] = [
  ['foreground', 'background'],
  ['foreground', 'card'],
  ['muted-foreground', 'background'],
  ['muted-foreground', 'card'],
  ['primary-foreground', 'primary'],
]

const HEX = /^#[0-9A-Fa-f]{6}$/

function onlyColors(colors: Record<string, string> | undefined): ThemeColors {
  const out: ThemeColors = {}
  for (const token of THEME_TOKENS) {
    const value = colors?.[token]
    // The server checks this too; a value that isn't a plain color never reaches the page.
    if (typeof value === 'string' && HEX.test(value)) out[token] = value
  }
  return out
}

/** The library id of a plugin's theme: `plugin:owner/name:key`. */
export function pluginThemeId(pluginId: string, key: string): string {
  return `plugin:${pluginId}:${key}`
}

/** A theme an installed plugin brings, in the shape the library uses. */
export function pluginTheme(
  plugin: { plugin_id: string; name: Record<string, string> },
  theme: { key: string; name: Record<string, string>; light?: Record<string, string>; dark?: Record<string, string>; radius?: number | null; font?: ThemeFont | null },
): Theme {
  return {
    id: pluginThemeId(plugin.plugin_id, theme.key),
    light: onlyColors(theme.light),
    dark: onlyColors(theme.dark),
    ...(typeof theme.radius === 'number' && theme.radius >= 0 && theme.radius <= 1 ? { radius: theme.radius } : {}),
    ...(theme.font === 'mono' || theme.font === 'serif' ? { font: theme.font } : {}),
    name: theme.name,
    plugin: { id: plugin.plugin_id, name: plugin.name },
  }
}
