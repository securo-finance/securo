import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { auth as authApi } from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { resolveLayout, type DashboardLayout } from '@/components/dashboard/widgets'

/**
 * The user's dashboard layout, stored in `user.preferences.dashboard_layout`
 * so it follows them across devices. Saving applies the new layout right
 * away and rolls back to the stored one if the request fails.
 */
export function useDashboardLayout() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const [pending, setPending] = useState<DashboardLayout | null>(null)

  const stored = user?.preferences?.dashboard_layout
  const layout = useMemo(() => pending ?? resolveLayout(stored), [pending, stored])

  const saveLayout = useCallback(async (next: DashboardLayout) => {
    setPending(next)
    try {
      const updated = await authApi.updateMe({
        preferences: { ...(user?.preferences ?? {}), dashboard_layout: next },
      })
      updateUser(updated)
    } catch {
      toast.error(t('dashboard.customize.saveError'))
    } finally {
      setPending(null)
    }
  }, [user, updateUser, t])

  return { layout, saveLayout }
}
