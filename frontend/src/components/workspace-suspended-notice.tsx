import { useEffect } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useWorkspace } from '@/contexts/workspace-context'

//: How often a suspended session asks whether the workspace came back.
const STATUS_RECHECK_MS = 30_000

export function WorkspaceSuspendedNotice() {
  const { t } = useTranslation()
  const { current, refresh } = useWorkspace()
  const suspended = current?.status === 'suspended'

  // Reactivating is the administrator's action, on another screen: this
  // session read "suspended" when it loaded the workspace list, so its
  // `canWrite` would stay false until a reload. Asking again while the
  // notice is up is what makes the workspace writable on its own (ADR 0052).
  // In the background: a foreground load would put the whole shell in its
  // loading state every 30 seconds, and a failed check would blank it.
  useEffect(() => {
    if (!suspended) return
    const timer = window.setInterval(() => {
      void refresh({ background: true })
    }, STATUS_RECHECK_MS)
    return () => window.clearInterval(timer)
  }, [suspended, refresh])

  if (!suspended) return null
  return (
    <div
      role="status"
      className="mb-5 flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300"
    >
      <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{t('workspaceStatus.notice')}</span>
    </div>
  )
}
