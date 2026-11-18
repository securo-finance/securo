import { useCallback, useLayoutEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTheme } from 'next-themes'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'

import { admin as adminApi, auth as authApi } from '@/lib/api'
import { applyThemeVars, BUILT_IN_THEMES, DEFAULT_THEME_ID, findTheme, themeVars } from '@/lib/themes'
import { useAuth } from '@/contexts/auth-context'
import { usePluginThemes } from '@/hooks/use-plugin-themes'

const themeWrites: { chain: Promise<unknown>; latest: number } = { chain: Promise.resolve(), latest: 0 }

export const DEFAULT_COLORS_QUERY_KEY = ['admin', 'default-colors']

/**
 * Keeps the page in the theme this person picked, in the current light or
 * dark mode, and lets them pick another. Securo's own theme also takes the
 * instance's accent colors.
 */
export function useAppTheme() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const { resolvedTheme } = useTheme()
  const mode = resolvedTheme === 'dark' ? 'dark' : 'light'
  const { themes: pluginThemes, loading } = usePluginThemes()
  const themes = [...BUILT_IN_THEMES, ...pluginThemes]
  const theme = findTheme(user?.theme, themes)
  // A plugin's theme, until the installed plugins load: keep what the page
  // shows rather than flashing Securo's own look. A theme whose plugin is
  // gone falls back to it once they have.
  const waiting = loading && !!user?.theme?.startsWith('plugin:') && theme.id !== user.theme

  const { data: accent } = useQuery({
    queryKey: DEFAULT_COLORS_QUERY_KEY,
    queryFn: adminApi.defaultColors,
    staleTime: Infinity,
  })

  useLayoutEffect(() => {
    if (waiting) return
    applyThemeVars(themeVars(theme, mode, accent))
  }, [theme, mode, accent, waiting])

  // Picks are saved one after another, so the server keeps the last one; only
  // the latest pick decides what the page shows if a save fails.
  const chooseTheme = useCallback(
    (id: string) => {
      if (!user) return
      const next = id === DEFAULT_THEME_ID ? null : id
      const seq = ++themeWrites.latest
      updateUser({ ...user, theme: next })
      themeWrites.chain = themeWrites.chain
        .catch(() => {})
        .then(() => authApi.updateMe({ theme: next }))
        .then(
          (saved) => {
            if (seq === themeWrites.latest) updateUser(saved)
          },
          () => {
            if (seq !== themeWrites.latest) return
            toast.error(t('library.saveFailed'))
            // Back to what the server has, which a failed save didn't change.
            authApi.me().then(updateUser).catch(() => {})
          },
        )
    },
    [user, updateUser, t],
  )

  return { themeId: theme.id, chooseTheme, accent, themes }
}
