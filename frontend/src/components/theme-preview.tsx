import { useTheme } from 'next-themes'

import { DEFAULT_THEME_ID, SECURO_COLORS, type AccentColors, type Theme, type ThemeColors } from '@/lib/themes'

const FONT_FAMILY = { mono: 'ui-monospace, Menlo, Consolas, monospace', serif: 'ui-serif, Georgia, serif' } as const

/**
 * A miniature of the app in a theme, in the current light or dark mode:
 * the page, a card with text, the primary color and the first chart series.
 * Tokens a theme doesn't set show Securo's own.
 */
export function ThemePreview({ theme, accent }: { theme: Theme; accent?: AccentColors }) {
  const { resolvedTheme } = useTheme()
  const mode = resolvedTheme === 'dark' ? 'dark' : 'light'
  const own = SECURO_COLORS[mode]
  const colors: ThemeColors = {
    ...own,
    ...(theme.id === DEFAULT_THEME_ID && accent?.[mode] ? { primary: accent[mode]! } : {}),
    ...theme[mode],
  }
  const pick = (token: keyof ThemeColors) => colors[token]
  const radius = `${(theme.radius ?? 0.625) * 0.6}rem`
  return (
    <span
      aria-hidden
      className="flex h-20 gap-1.5 overflow-hidden rounded-lg border p-1.5"
      style={{ background: pick('background'), borderColor: pick('border') }}
    >
      <span className="w-3 shrink-0 rounded-sm" style={{ background: pick('card') }} />
      <span
        className="flex flex-1 flex-col justify-between border p-1.5"
        style={{ background: pick('card'), borderColor: pick('border'), borderRadius: radius }}
      >
        <span className="space-y-1">
          {theme.font && theme.font !== 'sans' ? (
            // A theme with its own font shows it, instead of the first line of text.
            <span className="block text-[11px] font-semibold leading-none" style={{ color: pick('foreground'), fontFamily: FONT_FAMILY[theme.font] }}>
              Aa 123
            </span>
          ) : (
            <span className="block h-1.5 w-3/5 rounded-full" style={{ background: pick('foreground') }} />
          )}
          <span className="block h-1.5 w-2/5 rounded-full" style={{ background: pick('muted-foreground') }} />
        </span>
        <span className="flex items-end justify-between">
          <span className="h-3 w-8" style={{ background: pick('primary'), borderRadius: radius }} />
          <span className="flex items-end gap-0.5">
            {[0.5, 0.8, 0.65, 1].map((h, i) => (
              <span key={i} className="w-1.5 rounded-sm" style={{ height: `${h * 18}px`, background: pick('chart-1'), opacity: 0.5 + i * 0.15 }} />
            ))}
          </span>
        </span>
      </span>
    </span>
  )
}
