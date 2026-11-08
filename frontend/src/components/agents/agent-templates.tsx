import { useTranslation } from 'react-i18next'
import { Loader2, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AGENT_TEMPLATES, type AgentTemplate } from '@/lib/agent-templates'

interface Props {
  /** Creates an agent from the template (one click) or opens the form prefilled. */
  onCreate: (template: AgentTemplate) => void
  /** Id of the template whose one-click creation is in flight, if any. */
  busyId: string | null
  /** Disables every card, e.g. while the LLM connections are still loading. */
  disabled?: boolean
}

/**
 * "Start from a template" grid on the Agents page. Each card creates a
 * ready-made agent (tuned system prompt plus the tools it needs) in one
 * click; the resulting agent is a normal agent, editable afterwards.
 */
export function AgentTemplatesSection({ onCreate, busyId, disabled = false }: Props) {
  const { t } = useTranslation()

  return (
    <section className="mb-6" aria-label={t('agents.templates.title')}>
      <h2 className="text-sm font-semibold mb-1">{t('agents.templates.title')}</h2>
      <p className="text-sm text-muted-foreground mb-3 max-w-2xl">{t('agents.templates.subtitle')}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {AGENT_TEMPLATES.map((tpl) => {
          const Icon = tpl.icon
          const busy = busyId === tpl.id
          return (
            <div
              key={tpl.id}
              className="bg-card rounded-xl border border-border shadow-sm p-4 flex flex-col gap-3"
            >
              <div className="flex items-start gap-3">
                <div className="h-9 w-9 rounded-md bg-primary/10 text-primary flex items-center justify-center shrink-0">
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-semibold">{t(tpl.nameKey)}</div>
                  <p className="text-xs text-muted-foreground mt-0.5">{t(tpl.descriptionKey)}</p>
                </div>
              </div>
              <div className="mt-auto">
                <Button
                  size="sm"
                  variant="outline"
                  className="w-full gap-1.5"
                  disabled={disabled || busyId !== null}
                  onClick={() => onCreate(tpl)}
                >
                  {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                  {t('agents.templates.create')}
                </Button>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
