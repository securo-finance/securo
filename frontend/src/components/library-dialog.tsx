import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useTheme } from 'next-themes'
import { ArrowRight, Check, Minus, Plus, RotateCcw } from 'lucide-react'

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { useLayout } from '@/hooks/use-layout'
import { useAppTheme } from '@/hooks/use-app-theme'
import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { SURFACE_ENTRIES, type LibraryEntry, type LibraryKind, type Surface } from '@/lib/library'
import { BUILT_IN_THEMES, DEFAULT_THEME_ID, SECURO_COLORS, type AccentColors, type Theme, type ThemeColors } from '@/lib/themes'
import { cn } from '@/lib/utils'

const KINDS: LibraryKind[] = ['widgets', 'reports', 'themes']

/**
 * Everything someone can place or apply, by kind: widgets for the dashboard,
 * reports, themes. Adding and removing take effect at once; there's nothing
 * to confirm.
 */
export function LibraryDialog({
  kind,
  onKindChange,
  onClose,
}: {
  kind: LibraryKind
  onKindChange: (kind: LibraryKind) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { pluginsEnabled } = useFeatureFlags()

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] flex-col gap-0 p-0 sm:max-w-2xl">
        <DialogHeader className="px-6 pb-4 pt-6">
          <DialogTitle>{t('library.title')}</DialogTitle>
          <DialogDescription>{t('library.subtitle')}</DialogDescription>
        </DialogHeader>
        <Tabs value={kind} onValueChange={(value) => onKindChange(value as LibraryKind)} className="min-h-0 flex-1 gap-0">
          <div className="px-6">
            <TabsList className="w-full sm:w-fit">
              {KINDS.map((k) => (
                <TabsTrigger key={k} value={k} className="flex-1 sm:flex-none sm:px-4">
                  {t(`library.kind.${k}`)}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          {/* Tall enough for the longest list, so switching kinds doesn't make the dialog jump. */}
          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6 pt-4 sm:min-h-[26rem]">
            <TabsContent value="widgets" className="mt-0">
              <SurfaceItems surface="dashboard" />
            </TabsContent>
            <TabsContent value="reports" className="mt-0">
              <SurfaceItems surface="reports" />
            </TabsContent>
            <TabsContent value="themes" className="mt-0">
              <Themes />
            </TabsContent>
          </div>
        </Tabs>
        {pluginsEnabled && (
          <div className="flex items-center justify-between gap-3 border-t px-6 py-3.5">
            <p className="text-xs text-muted-foreground">{t(`library.moreHint.${kind}`)}</p>
            <Button
              variant="ghost"
              size="sm"
              className="shrink-0"
              onClick={() => {
                onClose()
                navigate('/plugins')
              }}
            >
              {t('library.browseCatalog')}
              <ArrowRight size={14} className="ml-1" />
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

/** Each item of a surface, added or not, with the switch that adds or removes it. */
function SurfaceItems({ surface }: { surface: Surface }) {
  const { t } = useTranslation()
  const { ids, save, reset, isCustom } = useLayout(surface)
  const placed = new Set(ids)
  // The reports page can't be left empty; the dashboard can.
  const lastReport = surface === 'reports' && ids.length === 1

  return (
    <div className="space-y-2">
      <ul className="divide-y rounded-xl border">
        {SURFACE_ENTRIES[surface].map((entry) => {
          const isPlaced = placed.has(entry.id)
          const locked = isPlaced && lastReport
          return (
            <li key={entry.id} className="flex items-center gap-3 px-4 py-3">
              <EntryIcon entry={entry} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium leading-tight">{t(entry.titleKey)}</p>
                <p className="mt-0.5 text-xs leading-snug text-muted-foreground">{t(entry.descriptionKey)}</p>
              </div>
              {isPlaced ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  disabled={locked}
                  title={locked ? t('library.lastReport') : undefined}
                  aria-label={t(`library.removeFrom.${surface}`, { name: t(entry.titleKey) })}
                  onClick={() => save(ids.filter((id) => id !== entry.id))}
                >
                  <Minus size={14} className="mr-1" />
                  {t('library.remove')}
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="shrink-0"
                  aria-label={t(`library.addTo.${surface}`, { name: t(entry.titleKey) })}
                  onClick={() => save([...ids, entry.id])}
                >
                  <Plus size={14} className="mr-1" />
                  {t('library.add')}
                </Button>
              )}
            </li>
          )
        })}
      </ul>
      {lastReport && <p className="px-1 text-xs text-muted-foreground">{t('library.lastReport')}</p>}
      {isCustom && (
        <div className="flex justify-end">
          <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={reset}>
            <RotateCcw size={14} className="mr-1" />
            {t('library.restoreDefault')}
          </Button>
        </div>
      )}
    </div>
  )
}

function EntryIcon({ entry }: { entry: LibraryEntry }) {
  const Icon = entry.icon
  return (
    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
      <Icon size={18} />
    </span>
  )
}

function Themes() {
  const { t } = useTranslation()
  const { themeId, chooseTheme, accent } = useAppTheme()
  return (
    <div role="group" aria-label={t('library.kind.themes')} className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {BUILT_IN_THEMES.map((theme) => {
        const selected = theme.id === themeId
        return (
          <button
            key={theme.id}
            type="button"
            aria-pressed={selected}
            onClick={() => chooseTheme(theme.id)}
            className={cn(
              'group rounded-xl border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              selected ? 'border-primary ring-1 ring-primary' : 'hover:border-foreground/25',
            )}
          >
            <ThemePreview theme={theme} accent={accent} />
            <span className="mt-2 flex items-center justify-between gap-2 px-1">
              <span className="truncate text-sm font-medium">{t(`library.theme.${theme.id}`)}</span>
              {selected && <Check size={14} className="shrink-0 text-primary" />}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/**
 * A miniature of the app in a theme, in the current light or dark mode:
 * the page, a card with text, the primary color and the first chart series.
 * Tokens a theme doesn't set show Securo's own.
 */
function ThemePreview({ theme, accent }: { theme: Theme; accent?: AccentColors }) {
  const { resolvedTheme } = useTheme()
  const mode = resolvedTheme === 'dark' ? 'dark' : 'light'
  const own = SECURO_COLORS[mode]
  const colors: ThemeColors = {
    ...own,
    ...(theme.id === DEFAULT_THEME_ID && accent?.[mode] ? { primary: accent[mode]! } : {}),
    ...theme[mode],
  }
  const pick = (token: keyof ThemeColors) => colors[token]
  const radius = `${(theme.radius ?? 0.625) * 0.6}rem`
  return (
    <span
      aria-hidden
      className="flex h-20 gap-1.5 overflow-hidden rounded-lg border p-1.5"
      style={{ background: pick('background'), borderColor: pick('border') }}
    >
      <span className="w-3 shrink-0 rounded-sm" style={{ background: pick('card') }} />
      <span
        className="flex flex-1 flex-col justify-between border p-1.5"
        style={{ background: pick('card'), borderColor: pick('border'), borderRadius: radius }}
      >
        <span className="space-y-1">
          <span className="block h-1.5 w-3/5 rounded-full" style={{ background: pick('foreground') }} />
          <span className="block h-1.5 w-2/5 rounded-full" style={{ background: pick('muted-foreground') }} />
        </span>
        <span className="flex items-end justify-between">
          <span className="h-3 w-8" style={{ background: pick('primary'), borderRadius: radius }} />
          <span className="flex items-end gap-0.5">
            {[0.5, 0.8, 0.65, 1].map((h, i) => (
              <span key={i} className="w-1.5 rounded-sm" style={{ height: `${h * 18}px`, background: pick('chart-1'), opacity: 0.5 + i * 0.15 }} />
            ))}
          </span>
        </span>
      </span>
    </span>
  )
}
