import { useTranslation } from 'react-i18next'

import { useDescribe } from '@/hooks/use-describe-plugin-items'
import { contributedKinds } from '@/lib/plugin-catalog'
import type { PluginCatalogEntry, PluginItemKind } from '@/types'

const PLAN_KINDS = new Set<string>(['category_groups', 'categories', 'rules'])

/** What a plugin brings, as one quiet line: "2 categories, 3 rules". */
export function usePluginSummary() {
  const { t } = useTranslation()
  const describe = useDescribe()
  return (entry: PluginCatalogEntry) => {
    const counts = Object.fromEntries(
      Object.entries(entry.contributes).filter(([kind]) => PLAN_KINDS.has(kind)),
    ) as Record<PluginItemKind, number>
    const others = contributedKinds(entry)
      .filter((kind) => !PLAN_KINDS.has(kind))
      .map((kind) => t(`plugins.kindName.${kind}`, { defaultValue: kind }))
    return [describe(counts), ...others].filter(Boolean).join(', ')
  }
}
