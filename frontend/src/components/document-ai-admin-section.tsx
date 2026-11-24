import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, CheckCircle2, HelpCircle, ScanText } from 'lucide-react'
import { documentAi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { DocumentAiAdminSettings } from '@/types'

const PROVIDERS = ['ollama', 'openai', 'anthropic', 'openai_compatible'] as const
const SELECT_CLASS = 'w-full rounded-md border border-border bg-card px-3 py-2 text-sm focus:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/30'
const PLACEHOLDER_URLS: Record<string, string> = {
  ollama: 'http://ollama:11434',
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
  openai_compatible: 'http://localhost:1234/v1',
}

type Form = { enabled: boolean; provider: string; base_url: string; model: string; api_key: string; vision: 'auto' | 'true' | 'false' }

function formFrom(settings: DocumentAiAdminSettings | undefined): Form {
  return {
    enabled: settings?.enabled ?? false,
    provider: settings?.provider ?? 'ollama',
    base_url: settings?.base_url ?? '',
    model: settings?.model ?? '',
    api_key: '',
    vision: settings?.vision ?? 'auto',
  }
}

/**
 * Admin Settings: turn document reading on for the instance and pick the
 * model it reads with. Stored in the app; the key is never sent back.
 */
export function DocumentAiAdminSection() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: settings } = useQuery({ queryKey: ['admin', 'document-ai'], queryFn: () => documentAi.getAdmin() })
  const [form, setForm] = useState<Form>(formFrom(undefined))
  const [loadedFrom, setLoadedFrom] = useState<DocumentAiAdminSettings | undefined>(undefined)
  if (settings && settings !== loadedFrom) {
    setLoadedFrom(settings)
    setForm(formFrom(settings))
  }

  const save = useMutation({
    mutationFn: (overrides: Partial<Form> & { clear_api_key?: boolean } = {}) => {
      const next = { ...form, ...overrides }
      return documentAi.updateAdmin({
        enabled: next.enabled,
        provider: next.provider || null,
        base_url: next.base_url || null,
        model: next.model || null,
        vision: next.vision,
        api_key: next.api_key || null,
        clear_api_key: overrides.clear_api_key ?? false,
      })
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['admin', 'document-ai'], data)
      // The switch shows or hides document reading for everyone.
      queryClient.invalidateQueries({ queryKey: ['app-info'] })
      queryClient.invalidateQueries({ queryKey: ['document-ai'] })
      toast.success(t('documentAi.admin.saved'))
    },
    onError: (error) => {
      const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : t('documentAi.settings.saveError'))
    },
  })

  const needsKey = form.provider === 'openai' || form.provider === 'anthropic'
  const needsUrl = form.provider === 'openai_compatible'
  const VisionIcon = settings?.detected_vision === 'yes' ? CheckCircle2 : settings?.detected_vision === 'no' ? AlertTriangle : HelpCircle
  const visionTone = settings?.detected_vision === 'yes'
    ? 'text-emerald-700 dark:text-emerald-400'
    : settings?.detected_vision === 'no' ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'

  return (
    <section className="mb-8 rounded-xl border border-border/60 bg-card p-6 space-y-4" data-testid="document-ai-admin">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <ScanText className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-base font-semibold">{t('documentAi.admin.title')}</h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t('documentAi.admin.description')}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Label htmlFor="document-ai-enabled" className="text-sm">{t('documentAi.admin.enable')}</Label>
          <Switch
            id="document-ai-enabled"
            checked={form.enabled}
            disabled={save.isPending}
            onCheckedChange={(checked) => { setForm({ ...form, enabled: checked }); save.mutate({ enabled: checked }) }}
          />
        </div>
      </div>

      {settings && (
        <div className="space-y-1 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs">
          {settings.configured ? (
            <p className={`flex items-start gap-1.5 ${visionTone}`}>
              <VisionIcon size={13} className="mt-px shrink-0" />
              <span>
                {t(`documentAi.settings.vision_${settings.detected_vision}`)}
                {settings.detected_vision_source !== 'none' && ` (${t(`documentAi.settings.visionSource_${settings.detected_vision_source}`)})`}
              </span>
            </p>
          ) : (
            <p className="flex items-start gap-1.5 text-amber-700 dark:text-amber-400">
              <AlertTriangle size={13} className="mt-px shrink-0" />
              <span>{t('documentAi.admin.notConfigured')}</span>
            </p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="document-ai-admin-provider">{t('documentAi.settings.provider')}</Label>
          <select
            id="document-ai-admin-provider"
            className={SELECT_CLASS}
            value={form.provider}
            onChange={(e) => setForm({ ...form, provider: e.target.value })}
          >
            {PROVIDERS.map(p => <option key={p} value={p}>{t(`documentAi.settings.provider_${p}`)}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="document-ai-admin-model">{t('documentAi.settings.model')}</Label>
          <Input
            id="document-ai-admin-model"
            value={form.model}
            placeholder={form.provider === 'anthropic' ? 'claude-haiku-5-5' : form.provider === 'openai' ? 'gpt-4o-mini' : 'qwen2.5vl:7b'}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="document-ai-admin-url">
            {t('documentAi.settings.baseUrl')}
            {!needsUrl && <span className="ms-1 font-normal text-muted-foreground">{t('documentAi.admin.optional')}</span>}
          </Label>
          <Input
            id="document-ai-admin-url"
            value={form.base_url}
            placeholder={PLACEHOLDER_URLS[form.provider]}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="document-ai-admin-key">
            {t('documentAi.settings.apiKey')}
            {!needsKey && <span className="ms-1 font-normal text-muted-foreground">{t('documentAi.admin.optional')}</span>}
          </Label>
          <Input
            id="document-ai-admin-key"
            type="password"
            autoComplete="off"
            value={form.api_key}
            disabled={settings ? !settings.can_store_secrets : false}
            placeholder={settings?.has_api_key ? t('documentAi.settings.keyStored') : ''}
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
          />
          {settings && !settings.can_store_secrets && (
            <p className="text-xs text-amber-700 dark:text-amber-400">{t('documentAi.admin.keyNeedsSecret')}</p>
          )}
        </div>
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor="document-ai-admin-vision">{t('documentAi.settings.readsImages')}</Label>
          <select
            id="document-ai-admin-vision"
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
          <Button variant="ghost" size="sm" disabled={save.isPending} onClick={() => save.mutate({ api_key: '', clear_api_key: true })}>
            {t('documentAi.settings.removeKey')}
          </Button>
        )}
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate({})}>
          {t('common.save')}
        </Button>
      </div>
    </section>
  )
}
