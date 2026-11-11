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

  const write = (next: string[] | null) =>
    queryClient.setQueryData<UiLayouts>(queryKey, (old) => ({
      dashboard: null,
      reports: null,
      ...old,
      [surface]: next?.map((id) => ({ id })) ?? null,
    }))

  // Optimistic: the page rearranges at once. Saves to one surface go out one
  // after another, so the server ends on the last arrangement made; a save
  // that fails re-reads what the server has, unless a newer one is coming.
  const chainKey = `${workspaceId}:${surface}`
  const mutation = useMutation({
    mutationFn: (next: string[] | null) => {
      const send = () => (next === null ? uiLayouts.reset(surface) : uiLayouts.save(surface, next))
      const queued = (writeChains.get(chainKey) ?? Promise.resolve()).catch(() => {}).then(send)
      writeChains.set(chainKey, queued)
      return queued
    },
    onMutate: async (next) => {
      await queryClient.cancelQueries({ queryKey })
      write(next)
      const seq = (latestWrite.get(chainKey) ?? 0) + 1
      latestWrite.set(chainKey, seq)
      return { seq }
    },
    onError: (_error, _next, context) => {
      toast.error(t('library.saveFailed'))
      if (context?.seq === latestWrite.get(chainKey)) void queryClient.invalidateQueries({ queryKey })
    },
  })

  const { mutate } = mutation
  const save = useCallback((next: string[]) => mutate(next), [mutate])
  const reset = useCallback(() => mutate(null), [mutate])

  return { ids, isCustom: saved !== null, ready: !isPending, save, reset }
}
