import { useTranslation } from 'react-i18next'
import { AlertCircle, Check, RotateCcw, Terminal } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { ProviderSettingStatus } from '@/lib/api'

type Props = {
  fieldKey: string
  field: ProviderSettingStatus['fields'][string]
  value: string | boolean | undefined
  disabled: boolean
  pending: boolean
  onChange: (value: string | boolean) => void
  onReset: () => void
}

export function ProviderSettingsField({ fieldKey, field, value, disabled, pending, onChange, onReset }: Props) {
  const { t } = useTranslation()
  const id = `credential-${fieldKey}`
  const isToggle = fieldKey === 'simplefin_enabled'
  const source = field.source === 'app' ? (isToggle ? 'providerSettings.app' : 'providerSettings.catalog.savedValue')
    : `providerSettings.catalog.${field.source === 'environment' ? 'environmentValue' : 'notSet'}`
  const label = t(`providerSettings.fields.${fieldKey}`, { defaultValue: fieldKey })
  const SourceIcon = field.source === 'environment' ? Terminal : Check
  const hasValue = field.configured || field.source === 'app'

  return (
    <div className="space-y-2.5">
      {isToggle ? (
        <div className="flex items-start justify-between gap-4 rounded-lg border border-border/60 bg-muted/20 p-4">
          <div className="space-y-1.5">
            <Label id={`${id}-label`} htmlFor={id} className="text-sm font-medium leading-relaxed">{t('providerSettings.catalog.simplefinTitle')}</Label>
            <p className="text-sm leading-relaxed text-muted-foreground">{t('providerSettings.catalog.simplefinDescription')}</p>
          </div>
          <Switch id={id} aria-labelledby={`${id}-label`} checked={Boolean(value ?? field.configured)} disabled={disabled} onCheckedChange={onChange} className="mt-1" />
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Label htmlFor={id} className="text-sm font-medium">{label}</Label>
            {!field.invalid && (
              <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                {field.source !== 'none' && <SourceIcon className="size-3" aria-hidden="true" />}
                {t(source)}
              </span>
            )}
          </div>
          {fieldKey === 'enable_banking_private_key' ? (
            <textarea
              id={id}
              aria-describedby={`${id}-help`}
              aria-invalid={field.invalid && !value}
              rows={5}
              autoComplete="off"
              spellCheck={false}
              disabled={disabled}
              className="block w-full resize-y rounded-lg border border-input bg-background px-3 py-2.5 font-mono text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 disabled:opacity-50 aria-invalid:border-destructive"
              value={String(value ?? '')}
              placeholder="-----BEGIN PRIVATE KEY-----"
              onChange={(event) => onChange(event.target.value)}
            />
          ) : (
            <Input
              id={id}
              aria-describedby={`${id}-help`}
              aria-invalid={field.invalid && !value}
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              disabled={disabled}
              className="h-11 rounded-lg bg-background shadow-none"
              value={String(value ?? '')}
              placeholder={t(`providerSettings.catalog.${hasValue ? 'replacementPlaceholder' : 'valuePlaceholder'}`)}
              onChange={(event) => onChange(event.target.value)}
            />
          )}
        </>
      )}
      <div id={`${id}-help`} className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        {field.invalid ? (
          <p className="flex items-start gap-1.5 text-xs leading-relaxed text-destructive">
            <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {t('providerSettings.invalid')}
          </p>
        ) : isToggle ? <span className="text-xs text-muted-foreground">{t(source)}</span> : null}
        {field.source === 'app' && (
          <Button type="button" variant="link" className="h-auto whitespace-normal p-0 text-left text-xs text-muted-foreground" disabled={pending} onClick={onReset}>
            <RotateCcw className="size-3" aria-hidden="true" />
            {t(`providerSettings.${field.environment_configured ? 'reset' : 'remove'}`)}
          </Button>
        )}
      </div>
    </div>
  )
}
