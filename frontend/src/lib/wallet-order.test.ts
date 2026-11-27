import { describe, expect, it } from 'vitest'
import type { AssetGroup } from '../types'
import { applyWalletOrder } from './wallet-order'

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
