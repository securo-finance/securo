import { useTranslation } from 'react-i18next'
import { ArrowUpCircle, Ban, Check, CircleSlash } from 'lucide-react'

import { TYPE_ICON } from '@/lib/plugin-format'
import { pluginTypes } from '@/lib/plugin-catalog'
import { cn } from '@/lib/utils'
import type { PluginCatalogEntry } from '@/types'

const PILL = 'inline-flex h-5 shrink-0 items-center gap-1 rounded-full px-2 text-[11px] font-medium [&_svg]:size-3'

/**
 * Where this workspace stands with a plugin, or why it can't be installed.
 * Nothing when it's simply available: that's the normal case.
 */
export function PluginStatus({ entry, className }: { entry: PluginCatalogEntry; className?: string }) {
  const { t } = useTranslation()
  if (entry.revoked) {
    return (
      <span className={cn(PILL, 'bg-destructive/10 text-destructive', className)} title={entry.revoked_reason ?? undefined}>
        <Ban />
        {t('plugins.revoked')}
      </span>
    )
  }
  const blocked = !entry.compatible || !entry.supported
  const reason = (
    <span className={cn(PILL, 'bg-muted text-muted-foreground', className)}>
      <CircleSlash />
      {entry.compatible ? t('plugins.unavailable') : t('plugins.needsVersion', { version: entry.min_securo_version })}
    </span>
  )
  if (entry.update_available) {
    // An update this Securo can't take says why, rather than offering nothing.
    if (blocked) return reason
    return (
      <span className={cn(PILL, 'bg-primary/10 text-primary', className)}>
        <ArrowUpCircle />
        {t('plugins.updateAvailable')}
      </span>
    )
  }
  if (entry.installed_version) {
    return (
      <span className={cn(PILL, 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400', className)}>
        <Check />
        {t('plugins.installed')}
      </span>
    )
  }
  if (blocked) return reason
  return null
}

export function PluginTypes({ entry }: { entry: PluginCatalogEntry }) {
  const { t } = useTranslation()
  return (
    <>
      {pluginTypes(entry).map((type) => {
        const Icon = TYPE_ICON[type]
        return (
          <span key={type} className="inline-flex items-center gap-1 font-medium text-foreground/70">
            <Icon size={12} />
            {t(`plugins.page.type.${type}`)}
          </span>
        )
      })}
    </>
  )
}
