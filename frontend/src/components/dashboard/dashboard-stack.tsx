// The one special case of the dashboard composition (design packet §6.4
// rule 2): spending-charts and balance-evolution share the original
// two-column grid iff they are adjacent in the resolved order (either
// order among themselves) — that reproduces today's grid exactly under
// the default layout (AC-5). Separated or alone, a chart renders
// standalone full-width in a min-h-[380px] wrapper so ResponsiveContainer
// still has a definite height.
import { Fragment, type ReactNode } from 'react'

import type { DashboardWidgetDefinition } from '@/components/dashboard/widgets'
import type { DashboardWidgetId } from '@/lib/dashboard-layout'

const CHART_PAIR: readonly DashboardWidgetId[] = [
  'spending-charts',
  'balance-evolution',
]

export interface DashboardStackProps {
  /** Resolved, visible-only widgets in display order. */
  ordered: readonly DashboardWidgetDefinition[]
  /** One rendered element per registered widget id. */
  sections: Record<DashboardWidgetId, ReactNode>
}

export function DashboardStack({ ordered, sections }: DashboardStackProps) {
  const nodes: ReactNode[] = []
  for (let index = 0; index < ordered.length; index++) {
    const current = ordered[index]
    const next = ordered[index + 1]
    const paired =
      next !== undefined &&
      CHART_PAIR.includes(current.id) &&
      CHART_PAIR.includes(next.id)

    if (paired) {
      nodes.push(
        <div
          key={`pair-${current.id}-${next.id}`}
          className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5"
          style={{ gridAutoRows: 'minmax(380px, auto)' }}
        >
          {sections[current.id]}
          {sections[next.id]}
        </div>,
      )
      index++ // consumed as a pair
    } else if (CHART_PAIR.includes(current.id)) {
      nodes.push(
        <div key={current.id} className="grid min-h-[380px] mb-5">
          {sections[current.id]}
        </div>,
      )
    } else {
      nodes.push(
        <Fragment key={current.id}>{sections[current.id]}</Fragment>,
      )
    }
  }
  return <>{nodes}</>
}
