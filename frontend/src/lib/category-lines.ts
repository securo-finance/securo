import { parseAmountInput } from '@/lib/format'
import type {
  Category,
  CategoryLineShareType,
  ProjectedTransaction,
  Transaction,
  TransactionCategoryLine,
  TransactionCategoryLinesInput,
} from '@/types'

/**
 * Splitting one transaction across several categories (#425).
 *
 * The editor keeps each part as typed text (amounts follow the display
 * locale's separators) and checks the sum in whole cents, the same rule the
 * backend applies: exact parts must add up to the amount to the cent, and
 * percentages to exactly 100.
 */

export interface CategoryLineRow {
  key: string
  categoryId: string
  amount: string
  percent: string
  /** The saved category, shown even before the category list loads. */
  category?: Category | null
}

let rowSeq = 0
export function newRowKey(): string {
  rowSeq += 1
  return `line-${rowSeq}`
}

export function emptyRow(): CategoryLineRow {
  return { key: newRowKey(), categoryId: '', amount: '', percent: '' }
}

export function isSplitByCategory(
  tx: Pick<Transaction, 'has_category_lines' | 'category_lines'> | null | undefined,
): boolean {
  return !!tx?.has_category_lines && (tx.category_lines?.length ?? 0) > 0
}

/** Rows for the editor, from the lines the API returned. */
export function rowsFromLines(
  lines: TransactionCategoryLine[],
  formatAmount: (value: number) => string,
): CategoryLineRow[] {
  return [...lines]
    .sort((a, b) => a.position - b.position)
    .map((line) => ({
      key: newRowKey(),
      categoryId: line.category_id,
      category: line.category ?? null,
      amount: formatAmount(Number(line.amount)),
      percent: line.share_pct != null ? String(Number(line.share_pct)) : '',
    }))
}

/** Whole cents of a typed amount, or null when it does not parse. */
export function rowCents(value: string, locale: string): number | null {
  if (value.trim() === '') return 0
  const parsed = parseAmountInput(value, locale)
  if (parsed == null || parsed < 0) return null
  return Math.round(parsed * 100)
}

/** Hundredths of a percent, or null when it does not parse. */
export function rowBasisPoints(value: string): number | null {
  const trimmed = value.trim().replace(',', '.')
  if (trimmed === '') return 0
  const parsed = Number(trimmed)
  if (!Number.isFinite(parsed) || parsed < 0) return null
  return Math.round(parsed * 100)
}

export interface CategoryLinesCheck {
  valid: boolean
  /** Left to allocate: cents in exact mode, hundredths of a percent in percent mode. */
  remaining: number
  reason: 'tooFewLines' | 'missingCategory' | 'duplicateCategory' | 'invalidValue' | 'zeroPart' | 'sumMismatch' | null
}

export function checkLines(
  rows: CategoryLineRow[],
  shareType: CategoryLineShareType,
  totalAmount: number,
  locale: string,
): CategoryLinesCheck {
  const target = shareType === 'exact' ? Math.round(Math.abs(totalAmount) * 100) : 10000
  const values = rows.map((row) =>
    shareType === 'exact' ? rowCents(row.amount, locale) : rowBasisPoints(row.percent),
  )
  const sum = values.reduce<number>((acc, v) => acc + (v ?? 0), 0)
  const remaining = target - sum
  const fail = (reason: CategoryLinesCheck['reason']): CategoryLinesCheck => ({
    valid: false,
    remaining,
    reason,
  })
  if (rows.length < 2) return fail('tooFewLines')
  if (rows.some((row) => !row.categoryId)) return fail('missingCategory')
  if (new Set(rows.map((row) => row.categoryId)).size !== rows.length) return fail('duplicateCategory')
  if (values.some((v) => v == null)) return fail('invalidValue')
  if (values.some((v) => v === 0)) return fail('zeroPart')
  if (remaining !== 0) return fail('sumMismatch')
  return { valid: true, remaining: 0, reason: null }
}

export function buildLinesPayload(
  rows: CategoryLineRow[],
  shareType: CategoryLineShareType,
  locale: string,
): TransactionCategoryLinesInput {
  return {
    share_type: shareType,
    lines: rows.map((row) =>
      shareType === 'exact'
        ? { category_id: row.categoryId, amount: (rowCents(row.amount, locale) ?? 0) / 100 }
        : { category_id: row.categoryId, share_pct: (rowBasisPoints(row.percent) ?? 0) / 100 },
    ),
  }
}

/** The category the backend gives back when a split is undone. */
export function largestLineCategoryId(lines: TransactionCategoryLine[]): string | null {
  let best: TransactionCategoryLine | null = null
  for (const line of lines) {
    if (
      !best ||
      Number(line.amount) > Number(best.amount) ||
      (Number(line.amount) === Number(best.amount) && line.position < best.position)
    ) {
      best = line
    }
  }
  return best?.category_id ?? null
}

/**
 * A split template's projected occurrence, shaped like a split row so the
 * same badge and parts list render for it. Null when the template is not split.
 */
export function projectedSplitSource(
  pt: Pick<ProjectedTransaction, 'recurring_id' | 'currency' | 'has_category_lines' | 'category_lines'>,
): Pick<Transaction, 'has_category_lines' | 'category_lines' | 'currency'> | null {
  if (!pt.has_category_lines || !pt.category_lines?.length) return null
  return {
    has_category_lines: true,
    currency: pt.currency,
    category_lines: pt.category_lines.map((part, position) => ({
      id: `${pt.recurring_id}-${position}`,
      category_id: part.category_id,
      amount: part.amount,
      share_type: 'exact',
      share_pct: null,
      position,
      notes: null,
      category: { name: part.category_name, icon: part.category_icon, color: part.category_color },
    })) as unknown as TransactionCategoryLine[],
  }
}
