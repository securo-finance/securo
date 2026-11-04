import { describe, expect, it } from 'vitest'

import { lineFromProduct, priceFor, productActions } from './product-utils'
import type { Product, ProductPrice } from '@/types'

function price(overrides: Partial<ProductPrice>): ProductPrice {
  return {
    id: 'p',
    product_id: 'prod',
    currency: 'USD',
    unit_price: '100.00',
    tax_rate: null,
    billing: 'one_time',
    interval: null,
    nickname: null,
    lookup_key: null,
    active: true,
    external_source: null,
    external_id: null,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function product(prices: ProductPrice[], overrides: Partial<Product> = {}): Product {
  return {
    id: 'prod',
    name: 'Consulting hour',
    description: null,
    kind: 'service',
    unit: 'h',
    active: true,
    origin: 'local',
    external_source: null,
    external_id: null,
    custom_fields: null,
    fiscal_refs: null,
    prices,
    created_at: '2026-01-01T00:00:00Z',
    invoice_count: 0,
    ...overrides,
  }
}

describe('priceFor', () => {
  it('offers a live price in the currency, one-time before recurring, oldest first', () => {
    const p = product([
      price({ id: 'monthly', billing: 'recurring', interval: 'monthly', created_at: '2026-01-01T00:00:00Z' }),
      price({ id: 'later', unit_price: '120.00', created_at: '2026-03-01T00:00:00Z' }),
      price({ id: 'first', unit_price: '110.00', created_at: '2026-02-01T00:00:00Z' }),
      price({ id: 'eur', currency: 'EUR' }),
      price({ id: 'dead', active: false, created_at: '2025-01-01T00:00:00Z' }),
    ])
    expect(priceFor(p, 'usd')?.id).toBe('first')
    expect(priceFor(p, 'EUR')?.id).toBe('eur')
    expect(priceFor(p, 'BRL')).toBeNull()
  })
})

describe('lineFromProduct', () => {
  const current = { description: 'typed', quantity: '3', unit_price: '9', unit: 'day', tax_rate: '5' }

  it('copies the product in and remembers where it came from', () => {
    const p = product([price({ id: 'usd', unit_price: '200.00', tax_rate: '10' })])
    expect(lineFromProduct(current, p, p.prices[0])).toEqual({
      description: 'Consulting hour',
      quantity: '3',
      unit: 'h',
      unit_price: '200.00',
      tax_rate: '10',
      product_id: 'prod',
      price_id: 'usd',
    })
  })

  it('leaves the amount for the person when there is no price in the currency', () => {
    const p = product([], { unit: null })
    expect(lineFromProduct(current, p, null)).toEqual({
      ...current,
      description: 'Consulting hour',
      product_id: 'prod',
      price_id: null,
    })
  })
})

describe('productActions', () => {
  it('archives what invoices name and deletes only what nothing names', () => {
    expect(productActions({ active: true, invoice_count: 2 })).toEqual({ canArchive: true, canRestore: false, canDelete: false })
    expect(productActions({ active: false, invoice_count: 0 })).toEqual({ canArchive: false, canRestore: true, canDelete: true })
  })
})
