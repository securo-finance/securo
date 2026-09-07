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
}

export type DrillDownTotals = {
  absTotal: number
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
 * Foreign-currency rows are converted through `amountPrimary`; when that is
 * missing the row is skipped rather than added as if its raw amount were
 * already in the user's currency. This matches how `get_summary` computes
 * `monthly_*_primary` on the backend.
 */
export function sumDrillDownTotals(
  items: DrillDownTotalsItem[],
  userCurrency: string,
): DrillDownTotals {
  return items.reduce<DrillDownTotals>(
    (totals, item) => {
      const amount = item.currency === userCurrency
        ? Math.abs(item.amount)
        : item.amountPrimary != null
          ? Math.abs(item.amountPrimary)
          : 0

      totals.absTotal += amount
      if (item.isProjected) totals.projectedTotal += amount
      else if (item.isPending) totals.pendingTotal += amount
      else totals.postedTotal += amount
      return totals
    },
    { absTotal: 0, postedTotal: 0, pendingTotal: 0, projectedTotal: 0 },
  )
}
