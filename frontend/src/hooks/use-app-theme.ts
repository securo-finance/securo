import { useCallback, useLayoutEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTheme } from 'next-themes'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'

import { admin as adminApi, auth as authApi } from '@/lib/api'
import { applyThemeVars, DEFAULT_THEME_ID, findTheme, themeVars } from '@/lib/themes'
import { useAuth } from '@/contexts/auth-context'

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
  const theme = findTheme(user?.theme)

  const { data: accent } = useQuery({
    queryKey: DEFAULT_COLORS_QUERY_KEY,
    queryFn: adminApi.defaultColors,
    staleTime: Infinity,
  })

  useLayoutEffect(() => {
    applyThemeVars(themeVars(theme, mode, accent))
  }, [theme, mode, accent])

  const chooseTheme = useCallback(
    async (id: string) => {
      if (!user) return
      const previous = user.theme ?? null
      const next = id === DEFAULT_THEME_ID ? null : id
      updateUser({ ...user, theme: next })
      try {
        updateUser(await authApi.updateMe({ theme: next }))
      } catch {
        updateUser({ ...user, theme: previous })
        toast.error(t('library.saveFailed'))
      }
    },
    [user, updateUser, t],
  )

  return { themeId: theme.id, chooseTheme, accent }
}
