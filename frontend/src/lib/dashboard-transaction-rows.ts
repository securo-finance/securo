import { shouldShowPendingBadge } from './transaction-status'
import type {
  ProjectedTransaction,
  SimilarTransactionGroup,
  Transaction,
  TransactionDisplayItem,
} from '../types'

type DashboardRowBase = {
  key: string
  description: string
  date: string
  type: 'debit' | 'credit'
  amount: number
  amountPrimary: number | null
  currency: string
  categoryIcon: string | null
  categoryName: string | null
  categoryColor: string | null
  accountId: string | null
  attachmentCount: number
  isShared: boolean
  parentTotal: number | null
  ownerShare: number | null
  groupId: string | null
  parentOwnerName: string | null
  groupName: string | null
  isIgnored: boolean
  installmentNumber: number | null
  totalInstallments: number | null
  showPendingBadge: boolean
  hasFxFallback: boolean
}

export type DashboardTransactionRow = DashboardRowBase & {
  kind: 'transaction'
  transaction: Transaction
}

export type DashboardProjectedRow = DashboardRowBase & {
  kind: 'projected'
  projected: ProjectedTransaction
}

export type DashboardSimilarGroupRow = DashboardRowBase & {
  kind: 'group'
  group: SimilarTransactionGroup
  children: DashboardTransactionRow[]
}

export type DashboardDisplayRow =
  | DashboardTransactionRow
  | DashboardProjectedRow
  | DashboardSimilarGroupRow

function transactionRow(
  transaction: Transaction,
  groupNameById: ReadonlyMap<string, string>,
): DashboardTransactionRow {
  const isShared = Boolean(transaction.is_shared)
  const amount = isShared && transaction.viewer_share != null
    ? Number(transaction.viewer_share)
    : Number(transaction.amount)
  const rawOwnerShare = !isShared && transaction.viewer_share != null
    ? Number(transaction.viewer_share)
    : null
  const ownerShare = rawOwnerShare != null
    && Math.abs(rawOwnerShare) !== Math.abs(Number(transaction.amount))
    ? rawOwnerShare
    : null
  const groupId = transaction.group_id ?? null

  return {
    kind: 'transaction',
    transaction,
    key: transaction.id,
    description: transaction.description,
    date: transaction.date,
    type: transaction.type,
    amount,
    amountPrimary: transaction.amount_primary != null ? Number(transaction.amount_primary) : null,
    currency: transaction.currency,
    categoryIcon: transaction.category?.icon ?? null,
    categoryName: transaction.category?.name ?? null,
    categoryColor: transaction.category?.color ?? null,
    accountId: transaction.account_id ?? null,
    attachmentCount: transaction.attachment_count ?? 0,
    isShared,
    parentTotal: isShared ? Number(transaction.amount) : null,
    ownerShare,
    groupId,
    parentOwnerName: isShared ? transaction.parent_owner_name ?? null : null,
    groupName: groupId ? groupNameById.get(groupId) ?? null : null,
    isIgnored: transaction.is_ignored,
    installmentNumber: transaction.installment_number,
    totalInstallments: transaction.total_installments,
    showPendingBadge: shouldShowPendingBadge(transaction),
    hasFxFallback: Boolean(transaction.fx_fallback),
  }
}

function projectedRow(projected: ProjectedTransaction): DashboardProjectedRow {
  return {
    kind: 'projected',
    projected,
    key: `proj-${projected.recurring_id}-${projected.date}`,
    description: projected.description,
    date: projected.date,
    type: projected.type,
    amount: projected.amount,
    amountPrimary: projected.amount_primary ?? null,
    currency: projected.currency,
    categoryIcon: projected.category_icon,
    categoryName: projected.category_name,
    categoryColor: projected.category_color ?? null,
    accountId: projected.account_id,
    attachmentCount: 0,
    isShared: false,
    parentTotal: null,
    ownerShare: null,
    groupId: null,
    parentOwnerName: null,
    groupName: null,
    isIgnored: false,
    installmentNumber: null,
    totalInstallments: null,
    showPendingBadge: false,
    hasFxFallback: false,
  }
}

function similarGroupRow(
  group: SimilarTransactionGroup,
  newestFirst: boolean,
  groupNameById: ReadonlyMap<string, string>,
): DashboardSimilarGroupRow {
  const groupId = group.group_id ?? null
  const orderedTransactions = [...group.transactions].sort((left, right) => newestFirst
    ? right.date.localeCompare(left.date)
    : left.date.localeCompare(right.date))
  return {
    kind: 'group',
    group,
    children: orderedTransactions.map(transaction => transactionRow(transaction, groupNameById)),
    key: `similar-${group.key}`,
    description: group.description,
    date: newestFirst ? group.latest_date : group.earliest_date,
    type: group.type,
    amount: group.total_amount,
    amountPrimary: group.amount_primary,
    currency: group.currency,
    categoryIcon: group.category?.icon ?? null,
    categoryName: group.category?.name ?? null,
    categoryColor: group.category?.color ?? null,
    accountId: group.account_id,
    attachmentCount: group.attachment_count,
    isShared: group.is_shared,
    parentTotal: group.parent_total,
    ownerShare: group.owner_share,
    groupId,
    parentOwnerName: group.parent_owner_name,
    groupName: groupId ? groupNameById.get(groupId) ?? null : null,
    isIgnored: group.is_ignored,
    installmentNumber: null,
    totalInstallments: null,
    showPendingBadge: group.has_pending_badge,
    hasFxFallback: group.has_fx_fallback,
  }
}

/** Build the Dashboard list in one direction from the API display contract. */
export function buildDashboardTransactionRows(
  actualItems: TransactionDisplayItem[],
  projectedTransactions: ProjectedTransaction[],
  newestFirst: boolean,
  groupNameById: ReadonlyMap<string, string>,
): DashboardDisplayRow[] {
  const actualRows = actualItems.map(item => item.kind === 'group'
    ? similarGroupRow(item, newestFirst, groupNameById)
    : transactionRow(item.transaction, groupNameById))
  const rows: DashboardDisplayRow[] = [
    ...actualRows,
    ...projectedTransactions.map(projectedRow),
  ]
  return rows.sort((left, right) => newestFirst
    ? right.date.localeCompare(left.date)
    : left.date.localeCompare(right.date))
}

export function expandDashboardRows(
  rows: DashboardDisplayRow[],
  expandedGroupKeys: ReadonlySet<string>,
): { row: DashboardDisplayRow; nested: boolean }[] {
  const expanded: { row: DashboardDisplayRow; nested: boolean }[] = []
  for (const row of rows) {
    expanded.push({ row, nested: false })
    if (row.kind === 'group' && expandedGroupKeys.has(row.group.key)) {
      row.children.forEach(child => expanded.push({ row: child, nested: true }))
    }
  }
  return expanded
}
