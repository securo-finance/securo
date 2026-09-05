import { describe, it, expect } from 'vitest'
import { applyMask, formatTaxId } from './tax-id'

describe('applyMask', () => {
  it('formats a CNPJ as the user types', () => {
    const mask = '##.###.###/####-##'
    expect(applyMask('1', mask)).toBe('1')
    expect(applyMask('112', mask)).toBe('11.2')
    expect(applyMask('11222333', mask)).toBe('11.222.333')
    expect(applyMask('11222333000181', mask)).toBe('11.222.333/0001-81')
  })

  it('formats a CPF', () => {
    expect(applyMask('52998224725', '###.###.###-##')).toBe('529.982.247-25')
  })

  it('ignores whatever the mask has no slot for', () => {
    // Pasting an already-formatted value must not double up the separators.
    expect(applyMask('11.222.333/0001-81', '##.###.###/####-##')).toBe('11.222.333/0001-81')
    // And a value longer than the mask is truncated rather than mangled.
    expect(applyMask('112223330001819999', '##.###.###/####-##')).toBe('11.222.333/0001-81')
  })

  it('leaves the value untouched when the kind has no mask', () => {
    // The escape hatch: a document no pack describes must not be reformatted
    // into something it is not.
    expect(applyMask('T1234567890123 (houjin bangou)', null)).toBe(
      'T1234567890123 (houjin bangou)',
    )
    expect(applyMask('DE123456789', null)).toBe('DE123456789')
  })

  it('is idempotent, so re-rendering a stored value does not drift', () => {
    const mask = '##-#######'
    const once = applyMask('123456789', mask)
    expect(applyMask(once, mask)).toBe(once)
  })
})

describe('formatTaxId', () => {
  it('renders a normalised value back through its mask', () => {
    // What the backend stores is digits only; the browser is what puts the
    // punctuation back.
    expect(formatTaxId('11222333000181', '##.###.###/####-##')).toBe('11.222.333/0001-81')
    expect(formatTaxId('ISENTO', null)).toBe('ISENTO')
  })
})
