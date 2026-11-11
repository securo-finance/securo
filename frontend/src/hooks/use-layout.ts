import { useCallback, useContext, useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'

import { uiLayouts, type UiLayouts } from '@/lib/api'
import { resolveLayout, type LayoutItem, type Surface } from '@/lib/library'
import { WorkspaceContext } from '@/contexts/workspace-context'

// Per workspace and surface: the write in flight, and how many were made.
const writeChains = new Map<string, Promise<unknown>>()
const latestWrite = new Map<string, number>()
// Per workspace and surface: saves not yet settled, and the last arrangement
// the server is known to have, which is what a failed save goes back to.
const pendingWrites = new Map<string, number>()
const confirmed = new Map<string, LayoutItem[] | null>()

interface LayoutWrite {
  next: LayoutItem[] | null
  workspace: string | null
}

/**
 * How this person arranged a surface in the current workspace, and the means
 * to change it. When they never made one, or it can't be read, it's Securo's
 * default. `ready` turns true once that's known, so a page can hold back data
 * for items that may turn out not to be there.
 */
export function useLayout(surface: Surface) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  // Read through the context object so a page still renders outside a WorkspaceProvider.
  const workspaceId = useContext(WorkspaceContext)?.current?.id ?? null
  const queryKey = useMemo(() => ['ui-layouts', workspaceId], [workspaceId])

  const { data, isPending } = useQuery({
    queryKey,
    queryFn: () => uiLayouts.get(workspaceId),
    staleTime: Infinity,
    retry: 1,
  })
  const saved = data?.[surface] ?? null
  const items = useMemo(() => resolveLayout(surface, saved), [surface, saved])
  const ids = useMemo(() => items.map((item) => item.id), [items])

  /**
   * Read the surface back from the server once its last save is done. If
   * that read fails too, a save that failed puts back what was shown before
   * it (it never reached the server); a save that went through keeps its
   * arrangement, which is what the server has.
   */
  const reconcile = (workspace: string | null, seq: number | undefined, failed: boolean) => {
    const chain = `${workspace}:${surface}`
    if (seq !== latestWrite.get(chain)) return
    const put = (value: LayoutItem[] | null) =>
      queryClient.setQueryData<UiLayouts>(['ui-layouts', workspace], (old) => ({
        dashboard: null,
        reports: null,
        ...old,
        [surface]: value,
      }))
    uiLayouts
      .get(workspace)
      .then((fresh) => {
        // A save made while this read was out decides instead, and this
        // older read no longer says what the server has.
        if (seq !== latestWrite.get(chain)) return
        confirmed.set(chain, fresh[surface])
        put(fresh[surface])
      })
      .catch(() => {
        if (failed && seq === latestWrite.get(chain) && confirmed.has(chain)) put(confirmed.get(chain) ?? null)
      })
  }

  // Optimistic: the page rearranges at once. Saves to one surface go out one
  // after another, so the server ends on the last arrangement made. Each save carries the workspace it was made in, so one that goes out late
  // (queued, or paused while offline) can't land in another workspace.
  const mutation = useMutation({
    mutationFn: ({ next, workspace }: LayoutWrite) => {
      const send = () =>
        next === null ? uiLayouts.reset(surface, workspace) : uiLayouts.save(surface, next, workspace)
      const chain = `${workspace}:${surface}`
      const queued = (writeChains.get(chain) ?? Promise.resolve()).catch(() => {}).then(send)
      writeChains.set(chain, queued)
      return queued
    },
    onMutate: async ({ next, workspace }) => {
      const key = ['ui-layouts', workspace]
      await queryClient.cancelQueries({ queryKey: key })
      const chain = `${workspace}:${surface}`
      // With nothing in flight, what's shown is what the server has.
      if (!pendingWrites.get(chain)) confirmed.set(chain, queryClient.getQueryData<UiLayouts>(key)?.[surface] ?? null)
      pendingWrites.set(chain, (pendingWrites.get(chain) ?? 0) + 1)
      queryClient.setQueryData<UiLayouts>(key, (old) => ({
        dashboard: null,
        reports: null,
        ...old,
        [surface]: next,
      }))
      const seq = (latestWrite.get(chain) ?? 0) + 1
      latestWrite.set(chain, seq)
      return { seq }
    },
    // Once the last save to a surface lands (or fails), that surface is read
    // back from the server, in case the cache was replaced meanwhile (a
    // workspace switch resets it). Only that surface: the other one may have
    // its own save still on the way.
    onSuccess: (_result, { next, workspace }, context) => {
      confirmed.set(`${workspace}:${surface}`, next)
      reconcile(workspace, context?.seq, false)
    },
    onError: (_error, { workspace }, context) => {
      toast.error(t('library.saveFailed'))
      reconcile(workspace, context?.seq, true)
    },
    onSettled: (_result, _error, { workspace }) => {
      const chain = `${workspace}:${surface}`
      pendingWrites.set(chain, Math.max(0, (pendingWrites.get(chain) ?? 1) - 1))
    },
  })

  const { mutate } = mutation
  const save = useCallback((next: LayoutItem[]) => mutate({ next, workspace: workspaceId }), [mutate, workspaceId])
  const reset = useCallback(() => mutate({ next: null, workspace: workspaceId }), [mutate, workspaceId])

  return { items, ids, isCustom: saved !== null, ready: !isPending, save, reset }
}
