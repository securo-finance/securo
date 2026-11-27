import { useCallback, useState } from 'react'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import type { Goal } from '@/types'

/**
 * Reads a saved chart preference, using the default when no valid choice is saved.
 *
 * @param {string} key - User, workspace, and goal-specific storage key.
 * @param {boolean} fallback - Initial expanded state for this goal.
 * @returns {boolean} Saved expanded state or the default.
 */
function readExpanded(key: string, fallback: boolean): boolean {
  const saved = localStorage.getItem(key)
  return saved === 'true' ? true : saved === 'false' ? false : fallback
}

/**
 * Remembers a goal chart's expanded state using the sidebar's localStorage pattern.
 * Dated goals default to open; undated goals default to collapsed.
 *
 * @param {Goal} goal - Goal whose chart preference should be loaded and saved.
 * @returns {{ expanded: boolean; toggleExpanded: () => void }} State and toggle action.
 */
export function useGoalChartState(goal: Goal): {
  expanded: boolean
  toggleExpanded: () => void
} {
  const { user } = useAuth()
  const { current } = useWorkspace()
  const storageKey = `securo.goalChart.expanded.${user?.id}.${current?.id}.${goal.id}`
  // the fallback is `false` if the target date is not set, meaning we automatically don't show
  // the chart if there's no target date. If set, we fallback to `true`.
  const fallback = Boolean(goal.target_date)
  const [state, setState] = useState(() => ({
    key: storageKey,
    expanded: readExpanded(storageKey, fallback),
  }))
  const expanded = state.key === storageKey ? state.expanded : readExpanded(storageKey, fallback)

  // Keep preferences separate if the active user, workspace, or goal changes.
  const toggleExpanded = useCallback(() => {
    setState(previous => {
      const current =
        previous.key === storageKey ? previous.expanded : readExpanded(storageKey, fallback)
      const next = !current
      try {
        localStorage.setItem(storageKey, String(next))
      } catch { /* quota / disabled */}
      return { key: storageKey, expanded: next }
    })
  }, [storageKey, fallback])

  return { expanded, toggleExpanded }
}
