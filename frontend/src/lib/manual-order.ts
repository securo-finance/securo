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

/** The most ids one reorder request may name: the API refuses longer lists. */
export const MAX_ORDERED_IDS = 1000

/**
 * The ids to send to move the item at `from` to `to`: only those whose slot
 * changes, from the moved item to its target, in their new order. The API
 * keeps every other item in its slot. Null when nothing moves, or when that
 * span alone passes the API's limit, so no request is made.
 */
export function movedSpan(ids: string[], from: number, to: number): string[] | null {
  if (from === to || from < 0 || to < 0 || from >= ids.length || to >= ids.length) return null
  const [low, high] = from < to ? [from, to] : [to, from]
  if (high - low + 1 > MAX_ORDERED_IDS) return null
  const span = ids.slice(low, high + 1)
  const [moved] = span.splice(from - low, 1)
  span.splice(to - low, 0, moved)
  return span
}

