import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { agents } from '@/lib/api'
import type { AgentToolHandle } from '@/lib/api'
import { useWorkspace } from '@/contexts/workspace-context'

export function ToolsSection({ agentId }: { agentId: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { canWrite } = useWorkspace()
  const { data, isLoading } = useQuery({
    queryKey: ['agent-tools', agentId],
    queryFn: () => agents.tools(agentId),
  })

  const [draft, setDraft] = useState<AgentToolHandle[]>(data?.tools ?? [])
  const [seededTools, setSeededTools] = useState(data?.tools)
  if (data?.tools !== seededTools) {
    setSeededTools(data?.tools)
    setDraft(data?.tools ?? [])
  }

  const save = useMutation({
    mutationFn: () => agents.setTools(agentId, draft.map((t) => ({ server: t.server, tool_name: t.name, enabled: t.enabled }))),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-tools', agentId] })
      toast.success(t('agents.tools.saved'))
    },
    onError: () => toast.error(t('agents.tools.saveFailed')),
  })

  if (isLoading) return <div className="text-sm text-muted-foreground">{t('agents.tools.loading')}</div>

  const grouped = draft.reduce<Record<string, AgentToolHandle[]>>((acc, tool) => {
    ;(acc[tool.server] ||= []).push(tool)
    return acc
  }, {})

  const dirty = draft.some((tool, i) => (data?.tools[i]?.enabled ?? true) !== tool.enabled)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">{t('agents.tools.title')}</h3>
          <p className="text-xs text-muted-foreground">{t('agents.tools.subtitle')}</p>
        </div>
        {canWrite && (
          <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? t('agents.tools.saving') : t('agents.tools.save')}
          </Button>
        )}
      </div>

      {Object.keys(grouped).length === 0 ? (
        <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground text-center">
          {t('agents.tools.empty')}
        </div>
      ) : (
        <div className="space-y-5">
          {Object.entries(grouped).map(([server, items]) => (
            <div key={server}>
              <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">{server}</div>
              <div className="rounded-lg border divide-y">
                {items.map((tool) => (
                  <div key={`${tool.server}.${tool.name}`} className="flex items-center gap-3 px-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium flex items-center gap-2">
                        {tool.name}
                        {tool.is_proposal && (
                          <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
                            {t('agents.tools.proposeBadge')}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground line-clamp-2">{tool.description}</div>
                    </div>
                    <Switch
                      checked={tool.enabled}
                      disabled={!canWrite}
                      onCheckedChange={(v) => {
                        setDraft((d) => d.map((x) => (x.server === tool.server && x.name === tool.name ? { ...x, enabled: !!v } : x)))
                      }}
                    />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
