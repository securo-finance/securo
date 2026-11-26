import type { AssetGroup } from '../types'

/** Wallet display order; the id breaks ties the way the backend does. */
export function compareWallets(a: AssetGroup, b: AssetGroup): number {
  return a.position - b.position || a.name.localeCompare(b.name) || a.id.localeCompare(b.id)
}

/**
 * Put the items named in `orderedIds` in that order, each taking one of the
 * slots the named items held, so an item that is not named (hidden by a
 * Collection filter, sold, archived) keeps its place. This is what the
 * backend saves. Returns `items` unchanged for an order it cannot apply.
 */
export function placeInOrder<T extends { id: string }>(items: T[], orderedIds: string[]): T[] {
  const byId = new Map(items.map(item => [item.id, item]))
  if (new Set(orderedIds).size !== orderedIds.length || orderedIds.some(id => !byId.has(id))) {
    return items
  }
  const named = new Set(orderedIds)
  const inNewOrder = orderedIds.map(id => byId.get(id)!).values()
  return items.map(item => named.has(item.id) ? inNewOrder.next().value! : item)
}

/** The wallets after a reorder, numbered from 0 as the backend numbers them. */
export function applyWalletOrder(wallets: AssetGroup[], orderedIds: string[]): AssetGroup[] {
  const sorted = wallets.slice().sort(compareWallets)
  const placed = placeInOrder(sorted, orderedIds)
  if (placed === sorted) return wallets
  return placed.map((wallet, position) => ({ ...wallet, position }))
}
