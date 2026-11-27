/**
 * A drill-down row, reduced to what the footer totals need. Kept structural
 * rather than importing the panel's DisplayItem so the arithmetic can be
 * tested without standing up the panel.
 */
export type DrillDownTotalsItem = {
  amount: number
  amountPrimary: number | null
  currency: string
  isPending: boolean
  isProjected: boolean
  type?: 'debit' | 'credit'
}

export type DrillDownTotals = {
  absTotal: number
  /** Credits minus debits, for panels that open a net figure. */
  netTotal: number
  postedTotal: number
  pendingTotal: number
  projectedTotal: number
}

/**
 * Split the rows the panel is showing into the buckets the footer reports.
 *
 * Every row lands in exactly one of posted, pending or projected, so the
 * three always add back up to `absTotal`. That invariant is the point: the
 * footer labels its bottom line "total shown", and a row the user can see in
 * the list has to be inside it. Bucketing on the row's own `isPending` and
 * `isProjected` flags, rather than on a persisted status, is what keeps
 * projections from falling through the gaps: they have no transaction behind
 * them, so a status test can never place them.
 *
 * A row counts its primary stamp whenever it has one: that is the value the
 * dashboard, the reports and the list summary total a row with, so the
 * footer agrees with the counter that opened the panel (#467), a typed
 * conversion included (ADR 0031). A row in the user's own currency without a
 * stamp counts its amount; a foreign row without one is skipped rather than
 * added as if its raw amount were already in the user's currency.
 */
export function sumDrillDownTotals(
  items: DrillDownTotalsItem[],
  userCurrency: string,
): DrillDownTotals {
  return items.reduce<DrillDownTotals>(
    (totals, item) => {
      const amount = item.amountPrimary != null
        ? Math.abs(item.amountPrimary)
        : item.currency === userCurrency
          ? Math.abs(item.amount)
          : 0

      totals.absTotal += amount
      totals.netTotal += item.type === 'credit' ? amount : -amount
      if (item.isProjected) totals.projectedTotal += amount
      else if (item.isPending) totals.pendingTotal += amount
      else totals.postedTotal += amount
      return totals
    },
    { absTotal: 0, netTotal: 0, postedTotal: 0, pendingTotal: 0, projectedTotal: 0 },
  )
}
