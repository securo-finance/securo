import { useContext } from 'react'
import { useQuery } from '@tanstack/react-query'

import { WorkspaceContext } from '@/contexts/workspace-context'
import { timezones as timezonesApi } from '@/lib/api'

/**
 * The timezones a signed-in person can pick from, plus the application
 * default a workspace follows when it has none of its own. The list never
 * changes, but the default can be changed by an administrator in another
 * browser, so it is refetched after a few minutes rather than kept for the
 * whole session; saving it here invalidates the query straight away.
 */
export function useTimezones() {
  return useQuery({
    queryKey: ['timezones'],
    queryFn: timezonesApi.list,
    staleTime: 5 * 60 * 1000,
  })
}

/**
 * The timezone the server keeps the active workspace's books in, or undefined
 * while that is still unknown. Read through the context object directly so a
 * component rendered outside a WorkspaceProvider (previews, tests) degrades to
 * "unknown" instead of throwing.
 */
export function useEffectiveTimezone(): string | undefined {
  const workspace = useContext(WorkspaceContext)
  const { data } = useTimezones()
  return workspace?.current?.timezone ?? data?.default ?? undefined
}
