import type {
  SimilarTransactionGroup,
  Transaction,
  TransactionDisplayItem,
} from '../types'
import { shouldShowPendingBadge } from './transaction-status'

function normalize(value: string | null | undefined): string {
  return (value ?? '').normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase()
}

function isIgnored(transaction: Transaction): boolean {
  return transaction.is_ignored || Boolean(transaction.category?.is_ignored)
}

function isTransfer(transaction: Transaction): boolean {
  return Boolean(
    transaction.transfer_pair_id
    || transaction.source === 'transfer'
    || transaction.source === 'settlement'
    || transaction.category?.treat_as_transfer,
  )
}

function displayAmount(transaction: Transaction): number {
  if (transaction.is_shared && transaction.viewer_share != null) {
    return Math.abs(Number(transaction.viewer_share))
  }
  return Math.abs(Number(transaction.amount))
}

function partitionKey(transaction: Transaction, sortBy?: string | null): string {
  return JSON.stringify([
    transaction.user_id,
    transaction.account_id,
    transaction.payee_id
      ? `id:${transaction.payee_id}`
      : `name:${normalize(transaction.payee_name ?? transaction.payee)}`,
    normalize(transaction.description),
    transaction.type,
    transaction.currency,
    transaction.status,
    isIgnored(transaction),
    isTransfer(transaction),
    Boolean(transaction.is_shared),
    transaction.group_id ?? null,
    sortBy === 'category' ? transaction.category_id : null,
  ])
}

function commonValue<T>(values: T[]): T | null {
  if (values.length === 0 || values.some(value => value !== values[0])) return null
  return values[0]
}

function buildGroup(key: string, transactions: Transaction[]): SimilarTransactionGroup {
  const first = transactions[0]
  const categoryIds = new Set(transactions.map(transaction => transaction.category_id))
  const notes = transactions.map(transaction => transaction.notes)
  const primaryAmounts = transactions.map(transaction =>
    !transaction.is_shared && transaction.amount_primary != null
      ? Math.abs(Number(transaction.amount_primary))
      : null)
  const ownerShares = transactions.map(transaction => {
    if (transaction.is_shared || transaction.viewer_share == null) return null
    const share = Math.abs(Number(transaction.viewer_share))
    return share === Math.abs(Number(transaction.amount)) ? null : share
  })
  const allPrimaryAmountsAvailable = primaryAmounts.every(amount => amount != null)
  const allOwnerSharesAvailable = ownerShares.every(amount => amount != null)

  return {
    kind: 'group',
    key,
    transactions,
    description: first.description,
    type: first.type,
    currency: first.currency,
    total_amount: transactions.reduce((total, transaction) => total + displayAmount(transaction), 0),
    amount_primary: allPrimaryAmountsAvailable
      ? primaryAmounts.reduce((total, amount) => total + (amount ?? 0), 0)
      : null,
    parent_total: transactions.every(transaction => transaction.is_shared)
      ? transactions.reduce((total, transaction) => total + Math.abs(Number(transaction.amount)), 0)
      : null,
    owner_share: allOwnerSharesAvailable
      ? ownerShares.reduce((total, amount) => total + (amount ?? 0), 0)
      : null,
    earliest_date: transactions.reduce(
      (earliest, transaction) => transaction.date < earliest ? transaction.date : earliest,
      first.date,
    ),
    latest_date: transactions.reduce(
      (latest, transaction) => transaction.date > latest ? transaction.date : latest,
      first.date,
    ),
    account_id: first.account_id,
    category_id: categoryIds.size === 1 ? first.category_id : null,
    category: categoryIds.size === 1 ? first.category : null,
    has_multiple_categories: categoryIds.size > 1,
    common_notes: commonValue(notes),
    has_notes: notes.some(Boolean),
    has_multiple_notes: new Set(notes).size > 1,
    attachment_count: transactions.reduce(
      (total, transaction) => total + (transaction.attachment_count ?? 0),
      0,
    ),
    status: first.status,
    is_ignored: isIgnored(first),
    is_transfer: isTransfer(first),
    is_shared: Boolean(first.is_shared),
    group_id: first.group_id ?? null,
    parent_owner_name: commonValue(
      transactions.map(transaction => transaction.parent_owner_name ?? null),
    ),
    has_pending_badge: transactions.some(shouldShowPendingBadge),
    has_fx_fallback: transactions.some(transaction => transaction.fx_fallback),
  }
}

/** Group the transactions already loaded for the current view or page. */
export function groupSimilarTransactions(
  transactions: Transaction[],
  enabled: boolean,
  sortBy?: string | null,
  sortDir: 'asc' | 'desc' = 'desc',
): TransactionDisplayItem[] {
  if (!enabled) {
    return transactions.map(transaction => ({ kind: 'transaction', transaction }))
  }

  const buckets = new Map<string, Transaction[]>()
  for (const transaction of transactions) {
    const key = partitionKey(transaction, sortBy)
    const bucket = buckets.get(key)
    if (bucket) bucket.push(transaction)
    else buckets.set(key, [transaction])
  }

  const items = Array.from(buckets, ([key, bucket]): TransactionDisplayItem =>
    bucket.length === 1
      ? { kind: 'transaction', transaction: bucket[0] }
      : buildGroup(key, bucket))

  // For every other column, the first occurrence of each bucket preserves the
  // server's requested order. Amount is the exception because a collapsed
  // row displays the sum of its children rather than any individual amount.
  if (sortBy !== 'amount') return items
  const direction = sortDir === 'asc' ? 1 : -1
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      const leftAmount = left.item.kind === 'group'
        ? left.item.total_amount
        : displayAmount(left.item.transaction)
      const rightAmount = right.item.kind === 'group'
        ? right.item.total_amount
        : displayAmount(right.item.transaction)
      return (leftAmount - rightAmount) * direction || left.index - right.index
    })
    .map(({ item }) => item)
}

export function formatSimilarTransactionDateRange(
  group: Pick<SimilarTransactionGroup, 'earliest_date' | 'latest_date'>,
  locale: string,
  referenceDate: Date = new Date(),
): string {
  const earliest = new Date(`${group.earliest_date}T00:00:00`)
  const latest = new Date(`${group.latest_date}T00:00:00`)
  const currentYear = referenceDate.getFullYear()
  const includeYear = earliest.getFullYear() !== currentYear || latest.getFullYear() !== currentYear
  const formatter = new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'short',
    ...(includeYear ? { year: 'numeric' } : {}),
  })
  if (group.earliest_date === group.latest_date) return formatter.format(earliest)
  if (typeof formatter.formatRange === 'function') return formatter.formatRange(earliest, latest)
  return `${formatter.format(earliest)} – ${formatter.format(latest)}`
}

export function flattenTransactionDisplayItems(
  items: TransactionDisplayItem[],
): Transaction[] {
  return items.flatMap(item =>
    item.kind === 'group' ? item.transactions : [item.transaction],
  )
}

export function visibleTransactionRows(
  items: TransactionDisplayItem[],
  expandedKeys: ReadonlySet<string>,
): Transaction[] {
  return items.flatMap(item => {
    if (item.kind === 'transaction') return [item.transaction]
    return expandedKeys.has(item.key) ? item.transactions : []
  })
}

export type GroupSelectionState = 'none' | 'some' | 'all'

export function similarGroupSelectionState(
  group: SimilarTransactionGroup,
  selectedIds: ReadonlySet<string>,
): GroupSelectionState {
  const selectable = group.transactions.filter(transaction => !transaction.is_shared)
  if (selectable.length === 0) return 'none'
  const selectedCount = selectable.filter(transaction => selectedIds.has(transaction.id)).length
  if (selectedCount === 0) return 'none'
  return selectedCount === selectable.length ? 'all' : 'some'
}
