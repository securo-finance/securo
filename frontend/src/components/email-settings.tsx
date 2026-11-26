import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertCircle, Mail, RotateCcw, Save } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { admin, type EmailTemplate } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'

// The languages the recovery message is written in, as the server keys them.
const LANGUAGES: { code: string; labelKey: string }[] = [
  { code: 'en', labelKey: 'admin.email.languageEn' },
  { code: 'pt', labelKey: 'admin.email.languagePt' },
]

const LINK = '{link}'

// Mirrors the server. The recovery link is the only thing the message may lead
// to, and it goes alone on its own line: anywhere else the text around it
// decides where the reader's click goes. A scheme, a `www.` host and a bare
// host name all count as an address.
const URL_PATTERN =
  /[a-z][a-z0-9+.-]*:\/\/|\b(?:https?|ftps?|file|javascript|vbscript):|www\.|\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b/i

// What the placeholders stand for while a template is checked, so both sides
// read the text as the reader gets it: `reset{minutes}.com` is no address in
// the template and `reset60.com` is one in the message.
const SAMPLE_LINK = 'https://securo.example/reset-password?token=check'

// `{{link}}` is the literal text `{link}`, which the server's formatter leaves
// alone: only an unescaped `{link}` is a field.
function linkFields(text: string): number {
  return text.replace(/\{\{/g, '').replace(/\}\}/g, '').split(LINK).length - 1
}

// `link` is what `{link}` stands for while the text is read. The body passes
// nothing, so the recovery link is not read as an address while one typed into
// the text by hand still is.
function asSent(text: string, link: string = SAMPLE_LINK): string {
  return text
    .replace(/\{\{/g, '\u0000')
    .replace(/\}\}/g, '\u0001')
    .split(LINK)
    .join(link)
    .split('{minutes}')
    .join('60')
    .split('{app_name}')
    .join('Securo')
    .split('\u0000')
    .join('{')
    .split('\u0001')
    .join('}')
}

type TemplateProblem = 'linkRequired' | 'linkOnce' | 'linkAlone' | 'linkInSubject' | 'otherLink'

function templateProblem(template: EmailTemplate | undefined): TemplateProblem | null {
  const subject = template?.subject ?? ''
  const body = template?.body ?? ''
  if (linkFields(subject) > 0) return 'linkInSubject'
  if (URL_PATTERN.test(asSent(subject))) return 'otherLink'
  const links = linkFields(body)
  if (links === 0) return 'linkRequired'
  if (links > 1) return 'linkOnce'
  if (body.split(/\r?\n/).filter((line) => line.trim() === LINK).length !== 1) return 'linkAlone'
  if (URL_PATTERN.test(asSent(body, ''))) return 'otherLink'
  return null
}

function sameTemplate(a?: EmailTemplate, b?: EmailTemplate): boolean {
  return (a?.subject ?? '') === (b?.subject ?? '') && (a?.body ?? '') === (b?.body ?? '')
}

export function EmailSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ['admin', 'email'],
    queryFn: admin.emailState,
  })
  // The edits stay next to the templates they were made from: when the server
  // answers with new templates (a save), the edits no longer match their
  // source and the fields fall back to what was saved, without an effect
  // syncing state on every refetch.
  const [edited, setEdited] = useState<{
    source: Record<string, EmailTemplate>
    drafts: Record<string, EmailTemplate>
  } | null>(null)
  const saved = data?.templates ?? {}
  const drafts = edited && edited.source === saved ? edited.drafts : saved

  const save = useMutation({
    mutationFn: (templates: Record<string, EmailTemplate>) => admin.updateEmailTemplates(templates),
    onSuccess: (state) => {
      queryClient.setQueryData(['admin', 'email'], state)
      // What was just saved is what the fields now show: an edit kept in the
      // draft would otherwise survive a restore that answers the same
      // templates, because the cached references do not change.
      setEdited(null)
      toast.success(t('admin.email.saved'))
    },
    onError: (error) => toast.error(extractApiError(error, t('admin.email.saveFailed'))),
  })

  const update = (code: string, field: keyof EmailTemplate, value: string) => {
    setEdited((state) => {
      const base = state && state.source === saved ? state.drafts : saved
      const template = base[code] ?? { subject: '', body: '' }
      return { source: saved, drafts: { ...base, [code]: { ...template, [field]: value } } }
    })
  }

  if (isError && !data) {
    return (
      <div role="alert" className="mb-8 flex flex-wrap items-center gap-3 rounded-xl border border-border/60 bg-card p-5">
        <AlertCircle className="size-5 text-destructive" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">{t('admin.email.title')}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('common.error')}</p>
        </div>
        <Button variant="outline" onClick={() => refetch()}>{t('common.retry')}</Button>
      </div>
    )
  }

  const dirty = !!data && LANGUAGES.some(({ code }) => !sameTemplate(drafts[code], data.templates[code]))
  const problems = dirty
    ? LANGUAGES.flatMap(({ code }) => {
        const problem = templateProblem(drafts[code])
        return problem ? [{ code, problem }] : []
      })
    : []
  const incomplete = LANGUAGES.some(({ code }) => !drafts[code]?.subject.trim() || !drafts[code]?.body.trim())
  const canSave = dirty && !incomplete && problems.length === 0 && !save.isPending

  return (
    <section aria-labelledby="admin-email-heading" className="mb-8 overflow-hidden rounded-xl border border-border/60 bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-border/40 px-5 py-4">
        <Mail className="size-4 text-muted-foreground" aria-hidden="true" />
        <h2 id="admin-email-heading" className="text-base font-semibold tracking-tight text-foreground">
          {t('admin.email.title')}
        </h2>
        {isPending ? (
          <Skeleton className="h-5 w-20 rounded-md" />
        ) : (
          <Badge
            variant="outline"
            className={data.enabled ? 'border-primary/15 bg-primary/5 text-primary' : 'text-muted-foreground'}
          >
            {t(`admin.email.${data.enabled ? 'enabled' : 'disabled'}`)}
          </Badge>
        )}
      </div>

      <div className="space-y-5 px-5 py-5">
        <p className="text-sm leading-relaxed text-muted-foreground">{t('admin.email.description')}</p>
        {data && !data.enabled && (
          <p role="status" className="rounded-lg border border-dashed border-border px-4 py-3 text-sm text-muted-foreground">
            {t('admin.email.disabledNote')}
          </p>
        )}

        {isPending ? (
          <div role="status" aria-label={t('common.loading')} className="space-y-3" aria-hidden="true">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-4 w-64" />
          </div>
        ) : (
          <dl className="grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {t('admin.email.provider')}
              </dt>
              <dd className="mt-1 text-sm text-foreground">
                {t(`admin.email.${data.provider === 'resend' ? 'providerResend' : 'providerSmtp'}`)}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {t('admin.email.sender')}
              </dt>
              <dd className="mt-1 break-all text-sm text-foreground">
                {data.sender || t('admin.email.senderMissing')}
              </dd>
            </div>
          </dl>
        )}

        {!isPending && (
          <form
            aria-label={t('admin.email.templatesTitle')}
            className="space-y-5"
            onSubmit={(event) => {
              event.preventDefault()
              if (canSave) save.mutate(drafts)
            }}
          >
            <div>
              <h3 className="text-sm font-semibold text-foreground">{t('admin.email.templatesTitle')}</h3>
              <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                {t('admin.email.templatesDescription')}
              </p>
            </div>

            {LANGUAGES.map(({ code, labelKey }) => (
              <fieldset key={code} className="space-y-3 rounded-lg border border-border/40 p-4">
                <legend className="px-1 text-sm font-medium text-foreground">{t(labelKey)}</legend>
                <div className="space-y-2">
                  <Label htmlFor={`email-subject-${code}`}>{t('admin.email.subject')}</Label>
                  <Input
                    id={`email-subject-${code}`}
                    value={drafts[code]?.subject ?? ''}
                    onChange={(event) => update(code, 'subject', event.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`email-body-${code}`}>{t('admin.email.body')}</Label>
                  <textarea
                    id={`email-body-${code}`}
                    rows={6}
                    className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
                    value={drafts[code]?.body ?? ''}
                    onChange={(event) => update(code, 'body', event.target.value)}
                  />
                </div>
                {problems
                  .filter((item) => item.code === code)
                  .map((item) => (
                    <p key={item.problem} role="alert" className="text-sm text-destructive">
                      {t(`admin.email.${item.problem}`)}
                    </p>
                  ))}
              </fieldset>
            ))}

            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                className="gap-2 shadow-none"
                onClick={() => save.mutate({})}
                disabled={save.isPending}
              >
                <RotateCcw className="size-3.5" aria-hidden="true" />
                {t('admin.email.restoreDefault')}
              </Button>
              <Button type="submit" className="gap-2" disabled={!canSave}>
                <Save className="size-3.5" aria-hidden="true" />
                {save.isPending ? t('common.loading') : t('admin.email.save')}
              </Button>
            </div>
          </form>
        )}
      </div>
    </section>
  )
}
