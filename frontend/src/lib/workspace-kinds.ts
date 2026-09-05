import type { WorkspaceKind } from '@/types'

/**
 * The workspace kinds, in the order they're offered at creation. Set
 * once and never edited, so this list only ever feeds the create dialog
 * and read-only labels.
 */
export const WORKSPACE_KINDS: readonly WorkspaceKind[] = ['personal', 'business']

export const WORKSPACE_KIND_LABEL_KEY: Record<WorkspaceKind, string> = {
  personal: 'workspace.kindPersonal',
  business: 'workspace.kindBusiness',
}

/** Fallback icon when a workspace hasn't picked its own. */
export const WORKSPACE_KIND_ICON: Record<WorkspaceKind, string> = {
  personal: 'user',
  business: 'building-2',
}
