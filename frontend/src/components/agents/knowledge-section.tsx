import { useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Pin, PinOff, Trash2, Upload as UploadIcon, FileText, AlertCircle, Loader2, CheckCircle2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { agents } from '@/lib/api'
import type { KnowledgeDoc } from '@/lib/api'
import { useWorkspace } from '@/contexts/workspace-context'

function StatusBadge({ d }: { d: KnowledgeDoc }) {
  const { t } = useTranslation()
  if (d.status === 'ready')
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="h-3.5 w-3.5" /> {t('agents.knowledge.ready')} · {t('agents.knowledge.chunks', { count: d.chunk_count })}
      </span>
    )
  if (d.status === 'failed')
    return (
      <span className="inline-flex items-center gap-1 text-xs text-rose-600 dark:text-rose-400" title={d.error || ''}>
        <AlertCircle className="h-3.5 w-3.5" /> {t('agents.knowledge.failed')}
      </span>
    )
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Loader2 className="h-3.5 w-3.5 animate-spin" /> {d.status}
    </span>
  )
}

export function KnowledgeSection({ agentId }: { agentId: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { canWrite } = useWorkspace()
  const fileRef = useRef<HTMLInputElement>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['agent-knowledge', agentId],
    queryFn: () => agents.knowledge.list(agentId),
    refetchInterval: (q) => {
      const items = q.state.data?.items || []
      return items.some((d) => d.status !== 'ready' && d.status !== 'failed') ? 3000 : false
    },
  })

  const upload = useMutation({
    mutationFn: (file: File) => agents.knowledge.upload(agentId, file, false),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-knowledge', agentId] })
      toast.success(t('agents.knowledge.uploaded'))
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail || t('agents.knowledge.uploadFailed'))
    },
  })

  const pin = useMutation({
    mutationFn: ({ id, pinned }: { id: string; pinned: boolean }) => agents.knowledge.pin(agentId, id, pinned),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-knowledge', agentId] }),
  })

  const remove = useMutation({
    mutationFn: (id: string) => agents.knowledge.remove(agentId, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-knowledge', agentId] }),
  })

  const items = data?.items ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">{t('agents.knowledge.title')}</h3>
          <p className="text-xs text-muted-foreground">{t('agents.knowledge.subtitle')}</p>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.md,.markdown,.txt,.rst"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) upload.mutate(f)
            if (fileRef.current) fileRef.current.value = ''
          }}
        />
        {canWrite && (
          <Button size="sm" onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
            <UploadIcon className="h-4 w-4 mr-1.5" />
            {upload.isPending ? t('agents.knowledge.uploading') : t('agents.knowledge.upload')}
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="text-sm text-muted-foreground">{t('agents.knowledge.loading')}</div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground text-center">
          {t('agents.knowledge.empty')}
        </div>
      ) : (
        <div className="rounded-lg border divide-y">
          {items.map((d) => (
            <div key={d.id} className="flex items-center gap-3 px-3 py-2.5">
              <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="text-sm truncate">{d.title}</div>
                <div className="flex items-center gap-3">
                  <StatusBadge d={d} />
                  <span className="text-xs text-muted-foreground">{(d.size_bytes / 1024).toFixed(0)} KB</span>
                </div>
              </div>
              {canWrite && (
                <>
                  <Button
                    size="icon"
                    variant="ghost"
                    title={d.pinned ? t('agents.knowledge.unpinTitle') : t('agents.knowledge.pinTitle')}
                    onClick={() => pin.mutate({ id: d.id, pinned: !d.pinned })}
                  >
                    {d.pinned ? <Pin className="h-4 w-4 text-amber-500" /> : <PinOff className="h-4 w-4" />}
                  </Button>
                  <Button size="icon" variant="ghost" onClick={() => remove.mutate(d.id)} title={t('common.delete')}>
                    <Trash2 className="h-4 w-4 text-rose-500" />
                  </Button>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
