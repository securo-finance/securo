import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check } from 'lucide-react'
import {
  CATEGORY_EMOJIS,
  CATEGORY_ICONS,
  extractFirstEmoji,
  getCategoryEmojiLabel,
  getCategoryIconLabel,
  isEmoji,
} from '@/lib/category-icons'
import { cn } from '@/lib/utils'

interface IconPickerProps {
  value: string
  color: string
  onChange: (iconName: string) => void
}

type Mode = 'icons' | 'emojis'

// The catalogue's own Portuguese label, searched alongside the active language
// so an emoji is reachable in either language whatever the UI shows.
const ICON_PT_LABELS: Record<string, string> = Object.fromEntries(
  CATEGORY_ICONS.map((entry) => [entry.name, entry.pt])
)

const GRID_CLASS = 'grid grid-cols-8 gap-1.5 max-h-48 overflow-y-auto p-1'

function tileClass(selected: boolean): string {
  return cn(
    'w-9 h-9 rounded-lg flex items-center justify-center transition-all',
    selected ? 'ring-2 ring-offset-1 ring-primary' : 'hover:bg-muted'
  )
}

// Search ignores case and accents, so "saude" finds "Saúde" and "seguro"
// finds an emoji whose keyword is written with an accent.
function normalize(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
}

export function IconPicker({ value, color, onChange }: IconPickerProps) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  // A saved emoji opens the picker on the emoji tab, so the current choice is
  // visible among the ones it can be changed to.
  const [mode, setMode] = useState<Mode>(() => (isEmoji(value) ? 'emojis' : 'icons'))

  const query = normalize(search.trim())
  const typedEmoji = extractFirstEmoji(search)

  const emojis = useMemo(
    () =>
      CATEGORY_EMOJIS.map((entry) => {
        const pt = entry.icon ? (ICON_PT_LABELS[entry.icon] ?? '') : ''
        const label = getCategoryEmojiLabel(t, entry)
        return {
          ...entry,
          label,
          haystack: normalize(
            [
              entry.emoji,
              label,
              ...entry.keywords,
              ...(entry.icon ? [entry.icon, pt] : []),
            ].join(' ')
          ),
        }
      }),
    [t]
  )

  const filteredIcons = useMemo(() => {
    if (!query) return CATEGORY_ICONS
    return CATEGORY_ICONS.filter(
      (entry) =>
        normalize(entry.name).includes(query) ||
        normalize(getCategoryIconLabel(t, entry.name)).includes(query)
    )
  }, [query, t])

  const filteredEmojis = useMemo(
    () => (query ? emojis.filter((entry) => entry.haystack.includes(query)) : emojis),
    [query, emojis]
  )

  return (
    <div className="space-y-2">
      <input
        type="text"
        placeholder={t('common.searchIconOrEmoji')}
        className="w-full border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div className="flex gap-1 p-0.5 rounded-lg bg-muted" role="group">
        {(['icons', 'emojis'] as const).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={mode === option}
            onClick={() => setMode(option)}
            className={cn(
              'flex-1 text-xs font-medium px-2 py-1 rounded-md transition-colors',
              mode === option
                ? 'bg-card text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {option === 'icons' ? t('common.iconPickerIcons') : t('common.iconPickerEmojis')}
          </button>
        ))}
      </div>
      {typedEmoji && (
        <button
          type="button"
          onClick={() => onChange(typedEmoji)}
          className="w-full flex items-center gap-3 rounded-lg border border-border px-3 py-2 text-sm bg-card hover:bg-muted/50 transition-colors text-start"
        >
          <span className="text-xl leading-none" aria-hidden="true">
            {typedEmoji}
          </span>
          <span className="flex-1">{t('common.useEmoji')}</span>
          {value === typedEmoji && <Check size={16} className="text-primary" />}
        </button>
      )}
      {mode === 'icons' && query && filteredEmojis.length > 0 && (
        <button
          type="button"
          onClick={() => setMode('emojis')}
          className="w-full text-xs text-start text-primary hover:underline px-1"
        >
          {t('common.seeMatchingEmojis', { count: filteredEmojis.length })}
        </button>
      )}
      {mode === 'icons' ? (
        <div className={GRID_CLASS}>
          {filteredIcons.map((entry) => {
            const isSelected = value === entry.name
            const Icon = entry.icon
            return (
              <button
                key={entry.name}
                type="button"
                title={getCategoryIconLabel(t, entry.name)}
                className={tileClass(isSelected)}
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
          {filteredIcons.length === 0 && !typedEmoji && (
            <p className="col-span-8 text-xs text-muted-foreground text-center py-4">
              {t('common.noIconsFound')}
            </p>
          )}
        </div>
      ) : (
        <div className={GRID_CLASS}>
          {filteredEmojis.map((entry) => {
            const isSelected = value === entry.emoji
            return (
              <button
                key={entry.emoji}
                type="button"
                title={entry.label}
                aria-label={entry.label}
                className={tileClass(isSelected)}
                style={isSelected ? { backgroundColor: color || '#6B7280' } : undefined}
                onClick={() => onChange(entry.emoji)}
              >
                <span className="text-lg leading-none" aria-hidden="true">
                  {entry.emoji}
                </span>
              </button>
            )
          })}
          {filteredEmojis.length === 0 && !typedEmoji && (
            <p className="col-span-8 text-xs text-muted-foreground text-center py-4">
              {t('common.noEmojisFound')}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
