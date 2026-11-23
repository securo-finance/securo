import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, CheckCircle2, HelpCircle, ScanText } from 'lucide-react'
import { documentAi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { DocumentAiSettings, DocumentAiSettingsUpdate } from '@/types'

const PROVIDERS = ['ollama', 'openai', 'anthropic', 'openai_compatible'] as const
const SELECT_CLASS = 'w-full rounded-md border border-border bg-card px-3 py-2 text-sm focus:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/30'

type Form = { provider: string; base_url: string; model: string; api_key: string; vision: 'auto' | 'true' | 'false' }

function formFrom(settings: DocumentAiSettings | undefined): Form {
  return {
    provider: settings?.provider ?? '',
    base_url: settings?.base_url ?? '',
    model: settings?.model ?? '',
    api_key: '',
    vision: settings?.vision ?? 'auto',
  }
}

/**
 * The model this workspace reads its documents with. Blank fields keep the
 * instance's defaults; owners only.
 */
export function DocumentAiSettingsSection() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: settings } = useQuery({ queryKey: ['document-ai', 'settings'], queryFn: documentAi.getSettings })
  const { data: status } = useQuery({ queryKey: ['document-ai', 'status'], queryFn: documentAi.status })
  const [form, setForm] = useState<Form>(formFrom(undefined))
  const [loadedFrom, setLoadedFrom] = useState<DocumentAiSettings | undefined>(undefined)
  if (settings && settings !== loadedFrom) {
    setLoadedFrom(settings)
    setForm(formFrom(settings))
  }

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['document-ai'] })
  }

  const save = useMutation({
    mutationFn: (clearKey: boolean) => {
      const payload: DocumentAiSettingsUpdate = {
        provider: form.provider || null,
        base_url: form.base_url || null,
        model: form.model || null,
        vision: form.vision,
        clear_api_key: clearKey,
      }
      if (form.api_key) payload.api_key = form.api_key
      return documentAi.updateSettings(payload)
    },
    onSuccess: () => { toast.success(t('documentAi.settings.saved')); refresh() },
    onError: () => toast.error(t('documentAi.settings.saveError')),
  })

  const reset = useMutation({
    mutationFn: documentAi.resetSettings,
    onSuccess: () => { toast.success(t('documentAi.settings.resetDone')); refresh() },
    onError: () => toast.error(t('documentAi.settings.saveError')),
  })

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

      {status && (
        <div className="space-y-1 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs">
          {status.configured ? (
            <>
              <p className="text-foreground">
                {t('documentAi.settings.current', {
                  provider: status.provider ? t(`documentAi.settings.provider_${status.provider}`) : '',
                  model: status.model ?? '',
                })}
                {' · '}
                {status.source === 'workspace' ? t('documentAi.settings.sourceWorkspace') : t('documentAi.settings.sourceInstance')}
              </p>
              <p className={`flex items-start gap-1.5 ${visionTone}`}>
                <VisionIcon size={13} className="mt-px shrink-0" />
                <span>
                  {t(`documentAi.settings.vision_${status.vision}`)}
                  {status.vision_source !== 'none' && ` (${t(`documentAi.settings.visionSource_${status.vision_source}`)})`}
                </span>
              </p>
            </>
          ) : (
            <p className="flex items-start gap-1.5 text-amber-700 dark:text-amber-400">
              <AlertTriangle size={13} className="mt-px shrink-0" />
              <span>{t('documentAi.errors.not_configured')}</span>
            </p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="document-ai-provider">{t('documentAi.settings.provider')}</Label>
          <select
            id="document-ai-provider"
            className={SELECT_CLASS}
            value={form.provider}
            onChange={(e) => setForm({ ...form, provider: e.target.value })}
          >
            <option value="">
              {t('documentAi.settings.useInstance', { value: settings?.instance_provider ?? t('documentAi.settings.none') })}
            </option>
            {PROVIDERS.map(p => <option key={p} value={p}>{t(`documentAi.settings.provider_${p}`)}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="document-ai-model">{t('documentAi.settings.model')}</Label>
          <Input
            id="document-ai-model"
            value={form.model}
            placeholder={settings?.instance_model ?? 'qwen2.5vl:7b'}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="document-ai-url">{t('documentAi.settings.baseUrl')}</Label>
          <Input
            id="document-ai-url"
            value={form.base_url}
            placeholder={settings?.instance_base_url ?? 'http://ollama:11434'}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="document-ai-key">{t('documentAi.settings.apiKey')}</Label>
          <Input
            id="document-ai-key"
            type="password"
            autoComplete="off"
            value={form.api_key}
            placeholder={settings?.has_api_key ? t('documentAi.settings.keyStored') : ''}
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
          />
        </div>
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor="document-ai-vision">{t('documentAi.settings.readsImages')}</Label>
          <select
            id="document-ai-vision"
            className={SELECT_CLASS}
            value={form.vision}
            onChange={(e) => setForm({ ...form, vision: e.target.value as Form['vision'] })}
          >
            <option value="auto">{t('documentAi.settings.visionAuto')}</option>
            <option value="true">{t('documentAi.settings.visionYes')}</option>
            <option value="false">{t('documentAi.settings.visionNo')}</option>
          </select>
          <p className="text-xs text-muted-foreground">{t('documentAi.settings.visionHint')}</p>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">{t('documentAi.settings.privacy')}</p>

      <div className="flex flex-wrap items-center justify-end gap-2">
        {settings?.has_api_key && (
          <Button variant="ghost" size="sm" disabled={save.isPending} onClick={() => save.mutate(true)}>
            {t('documentAi.settings.removeKey')}
          </Button>
        )}
        <Button variant="outline" size="sm" disabled={reset.isPending} onClick={() => reset.mutate()}>
          {t('documentAi.settings.reset')}
        </Button>
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate(false)}>
          {t('common.save')}
        </Button>
      </div>
    </section>
  )
}
