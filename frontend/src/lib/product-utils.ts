import type { InvoiceLineInput, Product, ProductPrice } from '@/types'

/**
 * Pure helpers for the catalog. The one rule they encode: a product
 * fills a line, it never owns it. `lineFromProduct` copies values in
 * and remembers where they came from; everything after that is the
 * line's own.
 */

/** The price to offer for a product in a currency: a live one in that
 *  currency, one-time before recurring, oldest first. Mirrors
 *  `product_service.price_for` on the server. */
export function priceFor(product: Product, currency: string): ProductPrice | null {
  const code = currency.toUpperCase()
  const candidates = product.prices
    .filter((p) => p.active && p.currency === code)
    .sort((a, b) => {
      if ((a.billing === 'one_time') !== (b.billing === 'one_time')) {
        return a.billing === 'one_time' ? -1 : 1
      }
      return a.created_at.localeCompare(b.created_at)
    })
  return candidates[0] ?? null
}

/** A line filled from a product. Keeps the row's quantity; a price in
 *  the invoice's currency fills the amount and the tax rate, and no
 *  price leaves them for the person to type. */
export function lineFromProduct(
  current: InvoiceLineInput,
  product: Product,
  price: ProductPrice | null,
): InvoiceLineInput {
  return {
    ...current,
    description: product.name,
    unit: product.unit ?? current.unit ?? null,
    unit_price: price ? price.unit_price : current.unit_price,
    tax_rate: price ? price.tax_rate : current.tax_rate,
    product_id: product.id,
    price_id: price?.id ?? null,
  }
}

/** Which actions a product offers. A product an invoice names is
 *  archived, never deleted; the server refuses the delete either way,
 *  so the button is not offered. */
export function productActions(product: Pick<Product, 'active' | 'invoice_count'>): {
  canArchive: boolean
  canRestore: boolean
  canDelete: boolean
} {
  return {
    canArchive: product.active,
    canRestore: !product.active,
    canDelete: product.invoice_count === 0,
  }
}
