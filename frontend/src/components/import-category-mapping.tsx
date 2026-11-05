import { useTranslation } from 'react-i18next'

import type { Category, CategoryGroup, PresetCategorySuggestion } from '@/types'

/**
 * What the user picked in the mapping card for one Monarch category value:
 * a category id, `null` = force Uncategorized, or 'rules' = contribute
 * nothing. Absence of a key means "no explicit choice yet" — the server's
 * suggestion applies as the default (§4.3).
 */
export type MappingChoice = string | null | 'rules'

interface ImportCategoryMappingProps {
  suggestions: PresetCategorySuggestion[]
  choices: Record<string, MappingChoice>
  categories: Category[]
  groups: CategoryGroup[]
  onChoose: (value: string, choice: MappingChoice) => void
}

/** The per-import remembered mapping card (§4.3): one row per distinct
    Monarch category value with its row count and the suggested target, plus
    the three allowed targets — any workspace category, Uncategorized
    (force_uncategorized), or Leave to my rules (no contribution). Sits
    between the CSV options panel and the review table. */
export function ImportCategoryMapping({
  suggestions,
  choices,
  categories,
  groups,
  onChoose,
}: ImportCategoryMappingProps) {
  const { t } = useTranslation()
  if (suggestions.length === 0) return null

  const byId = new Map(categories.map((c) => [c.id, c]))
  // Order options by group so the picker reads like the category sidebar.
  const grouped = groups
    .map((g) => ({ group: g, items: categories.filter((c) => c.group_id === g.id) }))
    .filter((entry) => entry.items.length > 0)
  const ungrouped = categories.filter((c) => !c.group_id || !groups.some((g) => g.id === c.group_id))

  function selectValueFor(value: string, suggestion: PresetCategorySuggestion): string {
    const choice = choices[value]
    if (choice === undefined) {
      return suggestion.suggested_category_id ?? 'rules'
    }
    if (choice === null) return '' // Uncategorized
    return choice
  }

  return (
    <div
      className="px-5 py-4 border-b border-border bg-muted/30"
      data-testid="category-mapping-card"
    >
      <p className="text-xs font-medium text-muted-foreground">{t('import.categoryMapping')}</p>
      <p className="text-xs text-muted-foreground mt-0.5 mb-3">
        {t('import.categoryMappingHint')}
      </p>
      <div className="space-y-2">
        {suggestions.map((s) => {
          const resolvedName =
            (choices[s.value] && byId.get(choices[s.value] as string)?.name) ??
            (choices[s.value] === undefined ? s.suggested_category_name : null)
          return (
            <div
              key={s.value}
              className="flex flex-col sm:flex-row sm:items-center gap-2"
              data-testid={`mapping-row-${s.value}`}
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm text-foreground truncate" title={s.value}>
                  {s.value}
                </p>
                <p className="text-xs text-muted-foreground">
                  {s.row_count} {t('import.mappingRows')}
                  {resolvedName ? ` · ${resolvedName}` : ''}
                </p>
              </div>
              <select
                className="border border-border rounded-md px-3 py-1.5 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px] sm:w-64"
                aria-label={t('import.mappingTargetFor', { value: s.value })}
                value={selectValueFor(s.value, s)}
                onChange={(e) => {
                  const v = e.target.value
                  if (v === 'rules') onChoose(s.value, 'rules')
                  else if (v === '') onChoose(s.value, null)
                  else onChoose(s.value, v)
                }}
              >
                <option value="rules">{t('import.leaveToRules')}</option>
                <option value="">{t('import.uncategorizedOption')}</option>
                {grouped.map(({ group, items }) => (
                  <optgroup key={group.id} label={group.name}>
                    {items.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
                {ungrouped.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
          )
        })}
      </div>
    </div>
  )
}
