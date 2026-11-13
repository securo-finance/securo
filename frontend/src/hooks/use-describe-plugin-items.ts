import { useTranslation } from 'react-i18next'
import type { PluginItemKind } from '@/types'

const KINDS: PluginItemKind[] = ['category_groups', 'categories', 'rules']

/** "1 group, 2 categories, 2 rules" for a list of items. */
export function useDescribe() {
  const { t } = useTranslation()
  return (items: { kind: PluginItemKind }[] | Record<PluginItemKind, number>) => {
    const counts = Array.isArray(items)
      ? Object.fromEntries(KINDS.map((kind) => [kind, items.filter((i) => i.kind === kind).length]))
      : items
    return KINDS.filter((kind) => counts[kind] > 0)
      .map((kind) => t(`plugins.kind.${kind}`, { count: counts[kind] }))
      .join(', ')
  }
}
