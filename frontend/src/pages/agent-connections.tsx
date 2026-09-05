import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, CheckCircle2, Edit2, Plug, Plus, Star, Trash2, XCircle } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { PageHeader } from '@/components/page-header'
import { agents } from '@/lib/api'
import type { LlmConnection } from '@/lib/api'
import { ConnectionFormDialog } from '@/components/agents/connection-form-dialog'
import { McpExternalPanel } from '@/components/agents/mcp-external-panel'
import { useWorkspace } from '@/contexts/workspace-context'

export default function AgentConnectionsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { canWrite } = useWorkspace()
  const [editing, setEditing] = useState<LlmConnection | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; detail: string }>>({})

  const { data: list, isLoading } = useQuery({
    queryKey: ['agent-connections'],
    queryFn: () => agents.connections.list(),
  })

  const removeMut = useMutation({
    mutationFn: (id: string) => agents.connections.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-connections'] })
      toast.success(t('agents.connections.deleted'))
    },
  })

  const testMut = useMutation({
    mutationFn: (id: string) => agents.connections.test(id),
    onSuccess: (res, id) => {
      setTestResults((r) => ({ ...r, [id]: { ok: res.ok, detail: res.detail } }))
    },
  })

  return (
    <div>
      <Link
        to="/agents"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-2 transition-colors"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> {t('agents.title')}
      </Link>

      <PageHeader
        section={t('agents.title')}
        title={t('agents.connections.title')}
        action={
          canWrite ? (
            <Button size="sm" className="gap-1.5 h-8" onClick={() => setCreateOpen(true)}>
              <Plus size={13} /> {t('agents.connections.add')}
            </Button>
          ) : undefined
        }
      />

      <p className="text-sm text-muted-foreground mb-6 max-w-2xl">{t('agents.connections.subtitle')}</p>

      {isLoading ? (
        <div className="bg-card rounded-xl border border-border shadow-sm p-6 text-sm text-muted-foreground">
          {t('agents.loading')}
        </div>
      ) : (list?.length ?? 0) === 0 ? (
        <div className="bg-card rounded-xl border border-border shadow-sm p-10 text-center">
          <div className="h-12 w-12 mx-auto rounded-full bg-muted flex items-center justify-center mb-3">
            <Plug className="h-6 w-6 text-muted-foreground" />
          </div>
          <h2 className="text-base font-semibold">{t('agents.connections.empty.title')}</h2>
          <p className="text-sm text-muted-foreground mt-1 max-w-md mx-auto">
            {t('agents.connections.empty.subtitle')}
          </p>
          {canWrite && (
            <Button size="sm" className="gap-1.5 h-8 mt-4" onClick={() => setCreateOpen(true)}>
              <Plus size={13} /> {t('agents.connections.add')}
            </Button>
          )}
        </div>
      ) : (
        <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
          <div className="divide-y divide-border">
            {list?.map((c) => {
              const tested = testResults[c.id]
              return (
                <div
                  key={c.id}
                  className="flex items-start gap-3 px-4 sm:px-5 py-4 hover:bg-muted/50 transition-colors"
                >
                  <div className="h-10 w-10 rounded-md bg-muted flex items-center justify-center shrink-0">
                    <Plug className="h-5 w-5 text-muted-foreground" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold truncate">{c.name}</span>
                      <Badge variant="secondary" className="text-[10px] uppercase tracking-wider px-1.5 py-0">
                        {c.kind}
                      </Badge>
                      {c.is_default && (
                        <Badge className="text-[10px] uppercase tracking-wider px-1.5 py-0 bg-amber-100 hover:bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200 inline-flex items-center gap-1">
                          <Star className="h-3 w-3" /> {t('agents.connections.default')}
                        </Badge>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5 truncate">
                      {c.base_url || t('agents.connections.providerDefaultEndpoint')} ·{' '}
                      {c.default_model || t('agents.connections.noDefaultModel')}
                    </div>
                    {tested && (
                      <div
                        className={`mt-2 inline-flex items-center gap-1.5 text-xs ${
                          tested.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'
                        }`}
                      >
                        {tested.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
                        {tested.detail}
                      </div>
                    )}
                  </div>
                  {canWrite && (
                    <div className="flex items-center gap-1 shrink-0">
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-8 text-xs"
                        onClick={() => testMut.mutate(c.id)}
                        disabled={testMut.isPending}
                      >
                        {t('agents.connections.test')}
                      </Button>
                      <button
                        type="button"
                        onClick={() => setEditing(c)}
                        className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                        title={t('common.edit')}
                      >
                        <Edit2 className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (confirm(t('agents.connections.deleteConfirm', { name: c.name }))) {
                            removeMut.mutate(c.id)
                          }
                        }}
                        className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition-colors"
                        title={t('common.delete')}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      <McpExternalPanel />

      <ConnectionFormDialog open={createOpen} onOpenChange={setCreateOpen} />
      <ConnectionFormDialog
        open={!!editing}
        onOpenChange={(o) => !o && setEditing(null)}
        connection={editing}
      />
    </div>
  )
}
