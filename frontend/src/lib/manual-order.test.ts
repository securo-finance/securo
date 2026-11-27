import { describe, expect, it } from 'vitest'
import type { AssetGroup } from '../types'
import { applyWalletOrder, MAX_ORDERED_IDS, movedSpan, placeInOrder } from './manual-order'

function wallet(id: string, position: number): AssetGroup {
  return {
    id,
    user_id: 'user',
    name: id,
    icon: 'wallet',
    color: '#000000',
    position,
    source: 'manual',
    connection_id: null,
    institution_name: null,
    asset_count: 0,
    current_value: 0,
    current_value_primary: 0,
  }
}

describe('applyWalletOrder', () => {
  it('reorders wallets and normalizes their positions', () => {
    const result = applyWalletOrder(
      [wallet('first', 4), wallet('second', 8), wallet('third', 12)],
      ['third', 'second', 'first'],
    ).sort((a, b) => a.position - b.position)

    expect(result.map(item => item.id)).toEqual(['third', 'second', 'first'])
    expect(result.map(item => item.position)).toEqual([0, 1, 2])
  })

  it('preserves the slots occupied by wallets outside a filtered subset', () => {
    const result = applyWalletOrder(
      [wallet('visible-a', 0), wallet('hidden', 1), wallet('visible-b', 2)],
      ['visible-b', 'visible-a'],
    ).sort((a, b) => a.position - b.position)

    expect(result.map(item => item.id)).toEqual(['visible-b', 'hidden', 'visible-a'])
  })

  it('ignores invalid orders', () => {
    const wallets = [wallet('first', 0), wallet('second', 1)]

    expect(applyWalletOrder(wallets, ['missing', 'first'])).toBe(wallets)
    expect(applyWalletOrder(wallets, ['first', 'first'])).toBe(wallets)
  })
})

describe('placeInOrder', () => {
  const rows = ['a', 'b', 'c', 'd'].map(id => ({ id }))

  it('trades only the slots of the named items', () => {
    expect(placeInOrder(rows, ['d', 'a']).map(row => row.id)).toEqual(['d', 'b', 'c', 'a'])
  })

  it('leaves the list alone for an order it cannot apply', () => {
    expect(placeInOrder(rows, ['a', 'missing'])).toBe(rows)
    expect(placeInOrder(rows, ['a', 'a'])).toBe(rows)
  })
})

describe('movedSpan', () => {
  const ids = ['a', 'b', 'c', 'd', 'e']

  it('names only the items between the moved one and its target', () => {
    expect(movedSpan(ids, 1, 3)).toEqual(['c', 'd', 'b'])
    expect(movedSpan(ids, 3, 1)).toEqual(['d', 'b', 'c'])
  })

  it('gives the same list as placing the whole new order', () => {
    const items = ids.map(id => ({ id }))
    const whole = ['a', 'c', 'd', 'b', 'e']
    expect(placeInOrder(items, movedSpan(ids, 1, 3)!)).toEqual(placeInOrder(items, whole))
  })

  it('sends nothing for a move that goes nowhere', () => {
    expect(movedSpan(ids, 2, 2)).toBeNull()
    expect(movedSpan(ids, 0, -1)).toBeNull()
    expect(movedSpan(ids, 4, 5)).toBeNull()
  })

  it('keeps a long list under the limit, and sends nothing past it', () => {
    const many = Array.from({ length: MAX_ORDERED_IDS + 1 }, (_, i) => `id-${i}`)
    expect(movedSpan(many, MAX_ORDERED_IDS, MAX_ORDERED_IDS - 1)).toHaveLength(2)
    expect(movedSpan(many, 0, MAX_ORDERED_IDS - 1)).toHaveLength(MAX_ORDERED_IDS)
    expect(movedSpan(many, 0, MAX_ORDERED_IDS)).toBeNull()
  })
})

