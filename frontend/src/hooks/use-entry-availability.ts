import { useCallback, useContext } from 'react'

import { useAssistant } from '@/contexts/assistant-context'
import { WorkspaceContext } from '@/contexts/workspace-context'
import type { LibraryEntry, WidgetEntry } from '@/lib/library'

/**
 * Why a library item can't be used here, as an i18n key, or null when it
 * can. A widget built on invoices needs a workspace that shows them (a
 * Business one); the agent widget needs AI agents on.
 */
export function useEntryAvailability(): (entry: LibraryEntry) => string | null {
  const { available: assistantAvailable } = useAssistant()
  // Read softly: outside a workspace (a test, a page before one is picked)
  // no module is shown, so nothing that needs one is offered.
  const workspace = useContext(WorkspaceContext)
  const hasModule = workspace?.hasModule
  return useCallback(
    (entry: LibraryEntry) => {
      const requires = (entry as WidgetEntry).requires
      if (!requires) return null
      if (requires === 'assistant') return assistantAvailable ? null : 'library.needs.assistant'
      return hasModule?.(requires) ? null : `library.needs.${requires}`
    },
    [assistantAvailable, hasModule],
  )
}
