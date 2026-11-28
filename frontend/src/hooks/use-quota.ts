import { useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { quotas as quotasApi } from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { quotaMessageKey, quotaMessageValues, readQuotaRefusal, safeInfoUrl } from '@/lib/quota'
import type { QuotaUsage, Workspace } from '@/types'

export const MY_QUOTAS_KEY = ['quotas', 'mine'] as const

type WorkspaceOwner = Pick<Workspace, 'created_by_user_id'> | null | undefined

/** The account's own usage limits (ADR 0049). Off instances answer with none. */
export function useMyQuotas() {
  return useQuery({ queryKey: MY_QUOTAS_KEY, queryFn: () => quotasApi.mine(), staleTime: 60_000 })
}

/**
 * Both hooks only read a context and throw when it is missing. The message
 * still reads right without them (it names the account's own limit), so a
 * dialog mounted outside those providers keeps working. Called on every
 * render either way, so the hook order never changes.
 */
function useOptional<T>(read: () => T): T | null {
  try {
    return read()
  } catch {
    return null
  }
}

/**
 * Says which usage limit filled and what frees a place. The workspace is the
 * one the action was in (the current one unless given): when someone else
 * created it, its connections and seats are counted on that person's limit,
 * and the message says so.
 */
export function useQuotaNotice() {
  const { t } = useTranslation()
  const userId = useOptional(useAuth)?.user?.id
  const currentWorkspace = useOptional(useWorkspace)?.current
  const queryClient = useQueryClient()
  const { data: quotas } = useMyQuotas()
  const infoUrl = safeInfoUrl(quotas?.info_url)

  const message = useCallback(
    (usage: QuotaUsage, workspace: WorkspaceOwner = currentWorkspace) => {
      const creator = workspace?.created_by_user_id
      const ownedByOther = !!creator && !!userId && creator !== userId
      return t(quotaMessageKey(usage.counter, ownedByOther, usage.maximum), quotaMessageValues(t, usage))
    },
    [t, userId, currentWorkspace],
  )

  const show = useCallback(
    (usage: QuotaUsage, workspace?: WorkspaceOwner) => {
      toast.error(message(usage, workspace ?? currentWorkspace), infoUrl
        ? { action: { label: t('quota.learnMore'), onClick: () => window.open(infoUrl, '_blank', 'noopener,noreferrer') } }
        : undefined)
    },
    [message, infoUrl, t, currentWorkspace],
  )

  /** Shows the message for a `quota_exceeded` refusal; false for any other error. */
  const handle = useCallback(
    (error: unknown, workspace?: WorkspaceOwner) => {
      const usage = readQuotaRefusal(error)
      if (!usage) return false
      void queryClient.invalidateQueries({ queryKey: MY_QUOTAS_KEY })
      show(usage, workspace)
      return true
    },
    [queryClient, show],
  )

  return { quotas, infoUrl, message, show, handle }
}
