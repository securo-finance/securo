// Merges the cumulative "invested" points into the portfolio trend rows. Each
// row gets `_invested`: the amount of the last point dated on or before the
// row (0 before the first). Both lists must be sorted ascending by date.
export function withInvested<T extends { date: string }>(
  rows: T[],
  points: { date: string; amount: number }[],
): (T & { _invested: number })[] {
  let i = 0
  let current = 0
  return rows.map((row) => {
    while (i < points.length && points[i].date <= row.date) {
      current = points[i].amount
      i += 1
    }
    return { ...row, _invested: current }
  })
}
