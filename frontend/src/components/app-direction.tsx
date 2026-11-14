import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Direction } from 'radix-ui'

/**
 * Tells Radix primitives (menus, popovers, selects, sliders) the reading
 * direction of the current language, so right-to-left languages open menus
 * and place submenus on the correct side. `<html dir>` itself is kept in sync
 * in lib/i18n.ts.
 */
export function AppDirection({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation()
  return (
    <Direction.DirectionProvider dir={i18n.dir(i18n.language)}>
      {children}
    </Direction.DirectionProvider>
  )
}
