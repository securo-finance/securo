import { useTranslation } from 'react-i18next'
import { FlaskConical } from 'lucide-react'
import { useFeatureFlags } from '@/hooks/use-feature-flags'

/**
 * The version rides in the banner so anyone (a visitor, marketing taking
 * screenshots, ops on a call) can tell which build the demo serves without
 * asking for the branch or reading the host. It comes from /api/info.
 */
export function DemoBanner() {
  const { t } = useTranslation()
  const { demoMode, version } = useFeatureFlags()
  if (!demoMode) return null
  return (
    <div className="fixed top-0 left-0 right-0 z-[70] flex h-7 items-center justify-center gap-2 bg-amber-500 px-4 text-[12px] font-medium text-amber-950">
      <FlaskConical size={14} className="shrink-0" />
      <span>{t('demo.banner')}</span>
      {version && (
        <span className="rounded bg-amber-950/15 px-1.5 py-0.5 font-mono text-[11px]" data-testid="demo-version">
          v{version}
        </span>
      )}
    </div>
  )
}
