import { useContext, useEffect, useRef, useState } from 'react'
import { WorkspaceRefreshStatusContext } from '@/contexts/workspace-context'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { CircleDot } from 'lucide-react'
import { workspaces } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import type { Workspace } from '@/types'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

type Value = 'needs_review' | 'reviewed'
type Policy = Record<'ingest' | 'manual', Value>
const keys = ['ingest', 'manual'] as const
const effective = (workspace: Workspace): Policy => ({
  ingest: workspace.review_policy?.ingest ?? 'needs_review',
  manual: workspace.review_policy?.manual ?? 'reviewed',
})
type Draft = {
  baseline: Policy
  draft: Policy
  server: Policy
  conflicts: Record<keyof Policy, boolean>
  generations: Record<keyof Policy, number>
}

function reconcile(state: Draft, server: Policy): Draft {
  const next = { ...state, baseline: { ...state.baseline }, draft: { ...state.draft }, server, conflicts: { ...state.conflicts } }
  for (const key of keys) {
    const b = state.baseline[key], d = state.draft[key], s = server[key]
    if (d === b || d === s) {
      next.draft[key] = s
      next.baseline[key] = s
      next.conflicts[key] = false
    } else if (s === b) {
      next.baseline[key] = s
      next.conflicts[key] = false
    } else {
      next.conflicts[key] = true
    }
  }
  return next
}

/** Keyed by workspace ID by its owner: an old workspace's save cannot edit a new draft. */
export function ReviewPolicySettings({ workspace, canManage, refresh }: {
  workspace: Workspace
  canManage: boolean
  refresh: () => Promise<void>
}) {
  const { t } = useTranslation()
  const refreshStatus = useContext(WorkspaceRefreshStatusContext)
  const mustReconcile = refreshStatus.pending || refreshStatus.failed
  const queryClient = useQueryClient()
  const server = effective(workspace)
  const [observed, setObserved] = useState(workspace)
  const [state, setState] = useState<Draft>(() => ({
    baseline: server, draft: server, server,
    conflicts: { ingest: false, manual: false }, generations: { ingest: 0, manual: 0 },
  }))
  if (observed !== workspace) {
    setObserved(workspace)
    setState((previous) => reconcile(previous, server))
  }
  const mounted = useRef(false)
  const inFlight = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const mutation = useMutation({
    mutationFn: (snapshot: Draft) => workspaces.update(workspace.id, {
      review_policy: { ...snapshot.draft }, expected_review_policy: { ...snapshot.baseline },
    }),
    onSuccess: (updated, snapshot) => {
      if (!mounted.current) return
      const returned = effective(updated)
      setState((previous) => {
        const draft = { ...previous.draft }
        for (const key of keys) {
          if (previous.generations[key] === snapshot.generations[key]) draft[key] = returned[key]
        }
        return { ...previous, baseline: returned, draft, server: returned, conflicts: { ingest: false, manual: false } }
      })
      toast.success(t('workspace.saveSuccess'))
      void refresh()
      void queryClient.invalidateQueries()
    },
    onError: (error) => {
      if (!mounted.current) return
      toast.error(extractApiError(error))
      if ((error as { response?: { status?: number } }).response?.status === 409) void refresh()
      // Never resubmit automatically: the next explicit save uses the reconciled baseline.
    },
    onSettled: () => { inFlight.current = false },
  })
  const conflicted = keys.some((key) => state.conflicts[key])
  const dirty = keys.some((key) => state.draft[key] !== state.baseline[key])

  return <section className="space-y-4 rounded-xl border bg-card p-6">
    <div className="flex items-center gap-2">
      <CircleDot className="h-4 w-4 text-muted-foreground" />
      <h2 className="text-base font-semibold">{t('workspace.reviewPolicyTitle')}</h2>
    </div>
    <p className="text-[11px] text-muted-foreground leading-relaxed">{t('workspace.reviewPolicyHint')}</p>
    <div className="grid gap-4 sm:grid-cols-2">
      {keys.map((key) => <div key={key} className="space-y-1.5">
        <label htmlFor={`ws-review-${key}`} className="text-sm font-medium">
          {t(key === 'ingest' ? 'workspace.reviewPolicyIngest' : 'workspace.reviewPolicyManual')}
        </label>
        <Select value={state.draft[key]} disabled={!canManage} onValueChange={(value) => {
          setState((previous) => ({ ...previous,
            draft: { ...previous.draft, [key]: value as Value },
            generations: { ...previous.generations, [key]: previous.generations[key] + 1 },
          }))
        }}>
          <SelectTrigger id={`ws-review-${key}`} className="h-10 rounded-lg w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="needs_review">{t('workspace.reviewPolicyNeedsReview')}</SelectItem>
            <SelectItem value="reviewed">{t('workspace.reviewPolicyReviewed')}</SelectItem>
          </SelectContent>
        </Select>
        {state.conflicts[key] && <div role="alert">
          {t('workspace.reviewPolicyServerValue', { defaultValue: 'Server value: {{value}}', value: state.server[key] })}
          <Button onClick={() => setState((previous) => ({ ...previous,
            baseline: { ...previous.baseline, [key]: previous.server[key] },
            draft: { ...previous.draft, [key]: previous.server[key] },
            conflicts: { ...previous.conflicts, [key]: false },
          }))}>{t('workspace.reviewPolicyUseServer', 'Use server')}</Button>
          <Button onClick={() => setState((previous) => ({ ...previous,
            baseline: { ...previous.baseline, [key]: previous.server[key] },
            conflicts: { ...previous.conflicts, [key]: false },
          }))}>{t('workspace.reviewPolicyKeepMine', 'Keep mine')}</Button>
        </div>}
      </div>)}
    </div>
    {refreshStatus.failed && <div role="alert">
      {t('workspace.reviewPolicyRefreshFailed', 'Unable to refresh workspace; refresh before saving. Your edits are retained.')}
      <Button disabled={refreshStatus.pending} onClick={() => { void refresh() }}>
        {t('workspace.reviewPolicyRefreshRetry', 'Refresh workspace')}
      </Button>
    </div>}
    {canManage && <div className="flex justify-end"><Button size="sm" className="rounded-lg"
      disabled={mutation.isPending || mustReconcile || conflicted || !dirty}
      onClick={() => {
        if (inFlight.current || mutation.isPending || mustReconcile || conflicted || !dirty) return
        inFlight.current = true
        mutation.mutate({ ...state, baseline: { ...state.baseline }, draft: { ...state.draft }, generations: { ...state.generations } })
      }}>{t('common.save', 'Save')}</Button></div>}
  </section>
}
