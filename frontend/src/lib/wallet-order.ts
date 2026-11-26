import type { AssetGroup } from '../types'

/** Wallet display order; the id breaks ties the way the backend does. */
export function compareWallets(a: AssetGroup, b: AssetGroup): number {
  return a.position - b.position || a.name.localeCompare(b.name) || a.id.localeCompare(b.id)
}

/**
 * Apply a partial wallet order while keeping unselected wallets in their
 * existing slots. The backend uses the same behavior when a Collection filter
 * means only some wallets are visible.
 */
export function applyWalletOrder(wallets: AssetGroup[], orderedIds: string[]): AssetGroup[] {
  if (orderedIds.length < 2) return wallets

  const walletsById = new Map(wallets.map(wallet => [wallet.id, wallet]))
  if (new Set(orderedIds).size !== orderedIds.length || orderedIds.some(id => !walletsById.has(id))) {
    return wallets
  }

  const selectedIds = new Set(orderedIds)
  const replacements = orderedIds.map(id => walletsById.get(id)!)
  let replacementIndex = 0
  const reordered = wallets
    .slice()
    .sort(compareWallets)
    .map(wallet => selectedIds.has(wallet.id) ? replacements[replacementIndex++] : wallet)
  const positions = new Map(reordered.map((wallet, position) => [wallet.id, position]))

  return wallets.map(wallet => ({ ...wallet, position: positions.get(wallet.id)! }))
}
