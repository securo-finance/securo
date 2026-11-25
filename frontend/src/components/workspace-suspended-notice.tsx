import { AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useWorkspace } from '@/contexts/workspace-context'

export function WorkspaceSuspendedNotice() {
  const { t } = useTranslation()
  const { current } = useWorkspace()
  if (current?.status !== 'suspended') return null
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
