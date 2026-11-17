import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Check, Minus, Plus, RotateCcw } from 'lucide-react'

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { useLayout } from '@/hooks/use-layout'
import { useEntryAvailability } from '@/hooks/use-entry-availability'
import { useAppTheme } from '@/hooks/use-app-theme'
import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { SURFACE_ENTRIES, type LibraryEntry, type LibraryKind, type Surface } from '@/lib/library'
import { ThemePreview } from '@/components/theme-preview'
import { localized } from '@/lib/localized-text'
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
  // Until the saved arrangement is in, a change would be made to the default and overwrite it.
  const { items, ids, save, reset, isCustom, ready } = useLayout(surface)
  const unavailable = useEntryAvailability()
  const placed = new Set(ids)
  // Everything is listed; what can't work in this workspace sits at the end, said why.
  const entries = [...SURFACE_ENTRIES[surface]].sort((a, b) => Number(unavailable(a) !== null) - Number(unavailable(b) !== null))
  // The reports page can't be left empty; the dashboard can.
  const lastReport = surface === 'reports' && ids.length === 1

  return (
    <div className="space-y-2">
      <ul className="divide-y rounded-xl border">
        {entries.map((entry) => {
          const isPlaced = placed.has(entry.id)
          const locked = isPlaced && lastReport
          const reason = unavailable(entry)
          return (
            <li key={entry.id} className="flex items-center gap-3 px-4 py-3" aria-disabled={reason !== null || undefined}>
              <div className={cn('flex min-w-0 flex-1 items-center gap-3', reason && 'opacity-60')}>
                <EntryIcon entry={entry} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium leading-tight">{t(entry.titleKey)}</p>
                  <p className="mt-0.5 text-xs leading-snug text-muted-foreground">{t(entry.descriptionKey)}</p>
                  {reason && <p className="mt-1 text-xs font-medium leading-snug text-muted-foreground">{t(reason)}</p>}
                </div>
              </div>
              {isPlaced ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  disabled={locked || !ready}
                  title={locked ? t('library.lastReport') : undefined}
                  aria-label={t(`library.removeFrom.${surface}`, { name: t(entry.titleKey) })}
                  onClick={() => save(items.filter((item) => item.id !== entry.id))}
                >
                  <Minus size={14} className="mr-1" />
                  {t('library.remove')}
                </Button>
              ) : (
                <Button
                  size="sm"
                  // A filled button reads as clickable even when faded; an unavailable one is drawn quiet.
                  variant={reason ? 'outline' : 'default'}
                  className="shrink-0"
                  disabled={!ready || reason !== null}
                  aria-label={t(`library.addTo.${surface}`, { name: t(entry.titleKey) })}
                  onClick={() => save([...items, { id: entry.id }])}
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
          <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={reset} disabled={!ready}>
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
  const { t, i18n } = useTranslation()
  const { themeId, chooseTheme, accent, themes } = useAppTheme()
  return (
    <div role="group" aria-label={t('library.kind.themes')} className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {themes.map((theme) => {
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
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">
                  {theme.name ? localized(theme.name, i18n.language) : t(`library.theme.${theme.id}`)}
                </span>
                {theme.plugin && (
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {t('library.fromPlugin', { name: localized(theme.plugin.name, i18n.language) })}
                  </span>
                )}
              </span>
              {selected && <Check size={14} className="shrink-0 text-primary" />}
            </span>
          </button>
        )
      })}
    </div>
  )
}

