import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, CheckCircle2, HelpCircle, ScanText } from 'lucide-react'
import { agents, documentAi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { DocumentAiSettings } from '@/types'

const SELECT_CLASS = 'w-full rounded-md border border-border bg-card px-3 py-2 text-sm focus:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/30'

type Form = { connection_id: string; model: string; vision: 'auto' | 'true' | 'false' }

function formFrom(settings: DocumentAiSettings | undefined): Form {
  return {
    connection_id: settings?.connection_id ?? '',
    model: settings?.model ?? '',
    vision: settings?.vision ?? 'auto',
  }
}

/**
 * Which of the owner's agents LLM connections reads this workspace's
 * documents. No connection: document reading is off here.
 */
export function DocumentAiSettingsSection() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: settings, isSuccess: settingsLoaded } = useQuery({ queryKey: ['document-ai', 'settings'], queryFn: () => documentAi.getSettings() })
  const { data: status } = useQuery({ queryKey: ['document-ai', 'status'], queryFn: () => documentAi.status() })
  const { data: connections = [] } = useQuery({ queryKey: ['agent-connections'], queryFn: () => agents.connections.list() })
  const [form, setForm] = useState<Form>(formFrom(undefined))
  const [loadedFrom, setLoadedFrom] = useState<DocumentAiSettings | undefined>(undefined)
  if (settings && settings !== loadedFrom) {
    setLoadedFrom(settings)
    setForm(formFrom(settings))
  }

  const save = useMutation({
    mutationFn: () => documentAi.updateSettings({
      connection_id: form.connection_id || null,
      model: form.model || null,
      vision: form.vision,
    }),
    onSuccess: () => {
      toast.success(t('documentAi.settings.saved'))
      queryClient.invalidateQueries({ queryKey: ['document-ai'] })
    },
    onError: () => toast.error(t('documentAi.settings.saveError')),
  })

  const selected = connections.find(c => c.id === form.connection_id)
  const VisionIcon = status?.vision === 'yes' ? CheckCircle2 : status?.vision === 'no' ? AlertTriangle : HelpCircle
  const visionTone = status?.vision === 'yes'
    ? 'text-emerald-700 dark:text-emerald-400'
    : status?.vision === 'no' ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'

  return (
    <section className="space-y-4 rounded-xl border bg-card p-6" data-testid="document-ai-settings">
      <div className="flex items-center gap-2">
        <ScanText className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-base font-semibold">{t('documentAi.settings.title')}</h2>
      </div>
      <p className="text-sm text-muted-foreground">{t('documentAi.settings.description')}</p>

      {status?.enabled && (
        <div className="space-y-1 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs">
          <p className="text-foreground">
            {t('documentAi.settings.current', { connection: status.connection_name ?? '', model: status.model ?? '' })}
          </p>
          <p className={`flex items-start gap-1.5 ${visionTone}`}>
            <VisionIcon size={13} className="mt-px shrink-0" />
            <span>
              {t(`documentAi.settings.vision_${status.vision}`)}
              {status.vision_source !== 'none' && ` (${t(`documentAi.settings.visionSource_${status.vision_source}`)})`}
            </span>
          </p>
        </div>
      )}

      {connections.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t('documentAi.settings.noConnections')}{' '}
          <Link to="/agents/connections" className="text-primary hover:underline">{t('documentAi.settings.manageConnections')}</Link>
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="document-ai-connection">{t('documentAi.settings.connection')}</Label>
            <select
              id="document-ai-connection"
              className={SELECT_CLASS}
              value={form.connection_id}
              // A model picked for another connection may not exist on this one.
              onChange={(e) => setForm({ ...form, connection_id: e.target.value, model: '' })}
            >
              <option value="">{t('documentAi.settings.off')}</option>
              {connections.map(c => (
                <option key={c.id} value={c.id}>{c.name} ({c.kind})</option>
              ))}
            </select>
            <Link to="/agents/connections" className="text-xs text-primary hover:underline">
              {t('documentAi.settings.manageConnections')}
            </Link>
          </div>
          <div className="space-y-1">
            <Label htmlFor="document-ai-model">{t('documentAi.settings.model')}</Label>
            <Input
              id="document-ai-model"
              value={form.model}
              disabled={!form.connection_id}
              placeholder={selected?.default_model
                ? t('documentAi.settings.modelPlaceholder', { model: selected.default_model })
                : 'qwen2.5vl:7b'}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
            />
          </div>
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor="document-ai-vision">{t('documentAi.settings.readsImages')}</Label>
            <select
              id="document-ai-vision"
              className={SELECT_CLASS}
              value={form.vision}
              disabled={!form.connection_id}
              onChange={(e) => setForm({ ...form, vision: e.target.value as Form['vision'] })}
            >
              <option value="auto">{t('documentAi.settings.visionAuto')}</option>
              <option value="true">{t('documentAi.settings.visionYes')}</option>
              <option value="false">{t('documentAi.settings.visionNo')}</option>
            </select>
            <p className="text-xs text-muted-foreground">{t('documentAi.settings.visionHint')}</p>
          </div>
        </div>
      )}
      <p className="text-xs text-muted-foreground">{t('documentAi.settings.privacy')}</p>

      {connections.length > 0 && (
        <div className="flex justify-end">
          {/* Until the saved choice is read, the form holds blanks: saving them would turn reading off. */}
          <Button size="sm" disabled={save.isPending || !settingsLoaded} onClick={() => save.mutate()}>
            {t('common.save')}
          </Button>
        </div>
      )}
    </section>
  )
}
