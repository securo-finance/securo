import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { CATEGORY_ICONS, iconLabel } from '@/lib/category-icons'
import { cn } from '@/lib/utils'

interface IconPickerProps {
  value: string
  color: string
  onChange: (iconName: string) => void
}

export function IconPicker({ value, color, onChange }: IconPickerProps) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return CATEGORY_ICONS
    return CATEGORY_ICONS.filter(
      (entry) =>
        entry.name.includes(q) ||
        entry.keywords.some((keyword) => keyword.includes(q))
    )
  }, [search])

  return (
    <div className="space-y-2">
      <input
        type="text"
        placeholder={t('common.searchIcon')}
        className="w-full border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {/* Taller than the 56-icon catalogue needed: 282 icons scroll a long way
          otherwise, and the search box above is the fast path anyway. */}
      <div className="grid grid-cols-8 gap-1.5 max-h-64 overflow-y-auto p-1">
        {filtered.map((entry) => {
          const isSelected = value === entry.name
          const Icon = entry.icon
          return (
            <button
              key={entry.name}
              type="button"
              title={iconLabel(entry.name)}
              className={cn(
                'w-9 h-9 rounded-lg flex items-center justify-center transition-all',
                isSelected
                  ? 'ring-2 ring-offset-1 ring-primary'
                  : 'hover:bg-muted'
              )}
              style={isSelected ? { backgroundColor: color || '#6B7280' } : undefined}
              onClick={() => onChange(entry.name)}
            >
              <Icon
                size={18}
                className={isSelected ? 'text-white' : 'text-muted-foreground'}
                strokeWidth={2}
              />
            </button>
          )
        })}
        {filtered.length === 0 && (
          <p className="col-span-8 text-xs text-muted-foreground text-center py-4">
            {t('common.noIconsFound')}
          </p>
        )}
      </div>
    </div>
  )
}
