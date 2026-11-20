import { useEffect } from 'react'
import { useTheme } from 'next-themes'

// Keep in sync with --sidebar in index.css: the mobile header is the surface
// the browser/OS chrome sits against.
const THEME_COLORS = { light: '#FFFFFF', dark: '#16171F' } as const

// The static <meta name="theme-color"> in index.html is only a first-paint
// default; this makes it follow the resolved light/dark theme.
export function ThemeColorSync() {
  const { resolvedTheme } = useTheme()

  useEffect(() => {
    const meta = document.querySelector('meta[name="theme-color"]')
    if (!meta) return
    meta.setAttribute('content', resolvedTheme === 'light' ? THEME_COLORS.light : THEME_COLORS.dark)
  }, [resolvedTheme])

  return null
}
