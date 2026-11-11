import { useCallback, useContext, useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'

import { uiLayouts, type UiLayouts } from '@/lib/api'
import { resolveLayout, type Surface } from '@/lib/library'
import { WorkspaceContext } from '@/contexts/workspace-context'

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

  // Optimistic: the page rearranges at once, and goes back if the save fails.
  const mutation = useMutation({
    mutationFn: (next: string[] | null) => (next === null ? uiLayouts.reset(surface) : uiLayouts.save(surface, next)),
    onMutate: async (next) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<UiLayouts>(queryKey)
      write(next)
      return { previous }
    },
    onError: (_error, _next, context) => {
      queryClient.setQueryData(queryKey, context?.previous)
      toast.error(t('library.saveFailed'))
    },
  })

  const { mutate } = mutation
  const save = useCallback((next: string[]) => mutate(next), [mutate])
  const reset = useCallback(() => mutate(null), [mutate])

  return { ids, isCustom: saved !== null, ready: !isPending, save, reset }
}
