/**
 * Shared "from → to" range formatting for the transactions filter bar
 * (chip and picker trigger) and the Reports custom date-range picker. Both
 * need the same three shapes — a closed range, and each one-sided bound —
 * differing only in whether the year is shown: the full form for a trigger
 * label with room to spare, the compact form for a tight chip or an active
 * segment where the year would just add noise.
 *
 * Callers are expected to guard the fully-empty case themselves (there is no
 * sensible range label for `from === '' && to === ''`), matching how the
 * transactions filter bar and date-range picker already gate on it before
 * calling in.
 */
export function formatDateRange(
  from: string,
  to: string,
  locale: string,
  { compact = false }: { compact?: boolean } = {},
): string {
  const fmt = (iso: string) => formatDate(iso, locale, { compact })
  if (from && to) return `${fmt(from)} - ${fmt(to)}`
  if (from) return `≥ ${fmt(from)}`
  return `≤ ${fmt(to)}`
}

/**
 * One ISO date (`YYYY-MM-DD`) in the same shape `formatDateRange` uses for
 * each bound, for places that show a single day. Parsed as local midnight so
 * the day does not shift with the viewer's timezone.
 */
export function formatDate(
  iso: string,
  locale: string,
  { compact = false }: { compact?: boolean } = {},
): string {
  return new Date(iso.slice(0, 10) + 'T00:00:00').toLocaleDateString(locale, {
    day: '2-digit',
    month: 'short',
    ...(compact ? {} : { year: 'numeric' as const }),
  })
}
