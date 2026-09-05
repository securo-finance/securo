import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Bot, ChevronRight, FileText, MessageSquare, Plug, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { PageHeader } from '@/components/page-header'
import { agents } from '@/lib/api'
import { AgentFormDialog } from '@/components/agents/agent-form-dialog'
import { useWorkspace } from '@/contexts/workspace-context'

export default function AgentsListPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { canWrite } = useWorkspace()
  const [createOpen, setCreateOpen] = useState(false)
  const { data: info } = useQuery({ queryKey: ['agents-info'], queryFn: () => agents.info() })
  const { data: list, isLoading } = useQuery({ queryKey: ['agents'], queryFn: () => agents.list() })
  const { data: connections } = useQuery({
    queryKey: ['agent-connections'],
    queryFn: () => agents.connections.list(),
  })
  // Setting an agent as default clears the flag on every other agent
  // server-side; we still need to refresh both queries so the badge
  // and the global-chat picker pick up the change immediately.
  const setDefaultMut = useMutation({
    mutationFn: ({ id, value }: { id: string; value: boolean }) =>
      agents.update(id, { is_default: value }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] })
      qc.invalidateQueries({ queryKey: ['agents-default'] })
    },
    onError: () => toast.error(t('agents.form.saveFailed')),
  })

  return (
    <div>
      <PageHeader
        section={t('agents.title')}
        title={t('agents.title')}
        action={
          canWrite ? (
            <div className="flex items-center gap-2">
              <Link to="/agents/connections">
                <Button variant="outline" size="sm" className="gap-1.5 h-8">
                  <Plug size={13} /> {t('agents.connections.manage')}
                </Button>
              </Link>
              <Button size="sm" className="gap-1.5 h-8" onClick={() => setCreateOpen(true)}>
                <Plus size={13} /> {t('agents.newAgent')}
              </Button>
            </div>
          ) : undefined
        }
      />

      <p className="text-sm text-muted-foreground mb-6 max-w-2xl">{t('agents.subtitle')}</p>

      {isLoading ? (
        <div className="bg-card rounded-xl border border-border shadow-sm p-6 text-sm text-muted-foreground">
          {t('agents.loading')}
        </div>
      ) : (list?.length ?? 0) === 0 ? (
        <div className="bg-card rounded-xl border border-border shadow-sm p-10 text-center">
          <div className="h-12 w-12 mx-auto rounded-full bg-muted flex items-center justify-center mb-3">
            <Bot className="h-6 w-6 text-muted-foreground" />
          </div>
          <h2 className="text-base font-semibold">{t('agents.empty.title')}</h2>
          <p className="text-sm text-muted-foreground mt-1 max-w-md mx-auto">{t('agents.empty.subtitle')}</p>
          {canWrite && (
            <Button size="sm" className="gap-1.5 h-8 mt-4" onClick={() => setCreateOpen(true)}>
              <Plus size={13} /> {t('agents.empty.create')}
            </Button>
          )}
        </div>
      ) : (
        <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
          <div className="divide-y divide-border">
            {list?.map((a) => (
              <div
                key={a.id}
                className="group flex items-center gap-3 px-4 sm:px-5 py-4 hover:bg-muted transition-colors"
              >
                <Link to={`/agents/${a.id}`} className="flex items-center gap-3 flex-1 min-w-0">
                  <div
                    className="h-10 w-10 rounded-md flex items-center justify-center text-white shrink-0"
                    style={{ backgroundColor: a.color }}
                  >
                    <Bot className="h-5 w-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="text-sm font-semibold truncate group-hover:text-primary transition-colors">
                        {a.name}
                      </div>
                      {a.is_default && (
                        <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-200/70 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200 shrink-0">
                          {t('agents.defaultBadge', 'Default')}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground truncate mt-0.5">
                      {(() => {
                        // Three layers of LLM config (most → least specific):
                        //   1. connection_id → user-managed LlmConnection row
                        //   2. provider + model → raw values, instance creds
                        //   3. nothing → instance default
                        // Show the most specific one we can resolve so the
                        // row tells the truth instead of always saying
                        // "Instance default · —" when the agent uses a
                        // connection.
                        const conn = a.connection_id
                          ? connections?.find((c) => c.id === a.connection_id)
                          : undefined
                        if (conn) {
                          const model = a.model || conn.default_model
                          return `${conn.name} · ${model || '—'}`
                        }
                        if (a.provider) {
                          return `${a.provider} · ${a.model || '—'}`
                        }
                        return t('agents.instanceDefault')
                      })()}
                    </div>
                    {a.description && (
                      <p className="text-sm text-muted-foreground mt-1 line-clamp-1">{a.description}</p>
                    )}
                    <div className="flex items-center gap-3 mt-1.5 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1" title={t('agents.conversationsCount', 'Conversations')}>
                        <MessageSquare className="h-3 w-3" />
                        {a.conversation_count ?? 0}
                      </span>
                      <span className="inline-flex items-center gap-1" title={t('agents.knowledgeCount', 'Knowledge files')}>
                        <FileText className="h-3 w-3" />
                        {a.knowledge_count ?? 0}
                      </span>
                    </div>
                  </div>
                </Link>
                {/* Default toggle — wrapper stops the click bubbling so
                    flipping the switch doesn't navigate into the agent. */}
                {canWrite && (
                  <div
                    className="flex items-center gap-2 shrink-0"
                    onClick={(e) => e.stopPropagation()}
                    title={t(
                      'agents.form.isDefaultHint',
                      'Used by the global slide-over chat (⌘J). Only one agent can be the default — turning this on clears it on others.',
                    )}
                  >
                    <span className="text-xs text-muted-foreground hidden sm:inline">
                      {t('agents.defaultLabel', 'Default')}
                    </span>
                    <Switch
                      checked={a.is_default}
                      onCheckedChange={(value) =>
                        setDefaultMut.mutate({ id: a.id, value: !!value })
                      }
                      disabled={setDefaultMut.isPending}
                      aria-label={t('agents.defaultLabel', 'Default')}
                    />
                  </div>
                )}
                <Link
                  to={`/agents/${a.id}`}
                  className="shrink-0 text-muted-foreground/60 hover:text-muted-foreground"
                  aria-label={t('common.view')}
                  title={t('common.view')}
                >
                  <ChevronRight className="h-4 w-4" />
                </Link>
              </div>
            ))}
          </div>
        </div>
      )}

      <AgentFormDialog open={createOpen} onOpenChange={setCreateOpen} providers={info?.providers ?? []} />
    </div>
  )
}
