type PendingBadgeTransaction = {
  source: string | null
  status: string | null
}

/** Bank-sync pending state already matches the provider and needs no UI badge. */
export function shouldShowPendingBadge(transaction: PendingBadgeTransaction): boolean {
  return transaction.status === 'pending' && transaction.source !== 'sync'
}
