import { useCallback, useContext, useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'

import { uiLayouts, type UiLayouts } from '@/lib/api'
import { resolveLayout, type Surface } from '@/lib/library'
import { WorkspaceContext } from '@/contexts/workspace-context'

// Per workspace and surface: the write in flight, and how many were made.
const writeChains = new Map<string, Promise<unknown>>()
const latestWrite = new Map<string, number>()

interface LayoutWrite {
  next: string[] | null
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

  const { data, isPending } = useQuery({ queryKey, queryFn: uiLayouts.get, staleTime: Infinity, retry: 1 })
  const saved = data?.[surface]?.map((item) => item.id) ?? null
  const ids = useMemo(() => resolveLayout(surface, saved), [surface, saved])

  // Optimistic: the page rearranges at once. Saves to one surface go out one
  // after another, so the server ends on the last arrangement made; a save
  // that fails re-reads what the server has, unless a newer one is coming.
  // Each save carries the workspace it was made in, so one that goes out late
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
      queryClient.setQueryData<UiLayouts>(key, (old) => ({
        dashboard: null,
        reports: null,
        ...old,
        [surface]: next?.map((id) => ({ id })) ?? null,
      }))
      const chain = `${workspace}:${surface}`
      const seq = (latestWrite.get(chain) ?? 0) + 1
      latestWrite.set(chain, seq)
      return { seq }
    },
    onError: (_error, { workspace }, context) => {
      toast.error(t('library.saveFailed'))
      if (context?.seq === latestWrite.get(`${workspace}:${surface}`)) {
        void queryClient.invalidateQueries({ queryKey: ['ui-layouts', workspace] })
      }
    },
  })

  const { mutate } = mutation
  const save = useCallback((next: string[]) => mutate({ next, workspace: workspaceId }), [mutate, workspaceId])
  const reset = useCallback(() => mutate({ next: null, workspace: workspaceId }), [mutate, workspaceId])

  return { ids, isCustom: saved !== null, ready: !isPending, save, reset }
}
