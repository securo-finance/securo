import { describe, expect, it } from 'vitest'

import {
  formatCurrency,
  resolveDateLocale,
  resolveDateOrder,
  resolveDisplayLocale,
} from '@/lib/format'

/**
 * Intl separates the symbol from the digits with a non-breaking or narrow
 * no-break space depending on locale and ICU version. Asserting on the raw
 * string makes these tests fail on a Node upgrade rather than on a real
 * regression, so collapse every kind of space to a plain one.
 */
function normalize(value: string): string {
  // JS \s already covers U+00A0 and the narrow U+202F that Intl emits.
  return value.replace(/\s/g, ' ')
}

describe('resolveDisplayLocale', () => {
  it('honours an explicit number format over the currency', () => {
    expect(resolveDisplayLocale('dot_comma', 'USD')).toBe('de-DE')
    expect(resolveDisplayLocale('comma_dot', 'BRL')).toBe('en-US')
    expect(resolveDisplayLocale('space_comma', 'BRL')).toBe('fr-FR')
  })

  it('derives the locale from the currency when the format is auto', () => {
    expect(resolveDisplayLocale('auto', 'BRL')).toBe('pt-BR')
    expect(resolveDisplayLocale('auto', 'EUR')).toBe('de-DE')
    expect(resolveDisplayLocale('auto', 'JPY')).toBe('ja-JP')
  })

  it('treats a missing number format like auto', () => {
    expect(resolveDisplayLocale(undefined, 'BRL')).toBe('pt-BR')
  })

  it('falls back when the currency is unknown or absent', () => {
    expect(resolveDisplayLocale('auto', 'XYZ')).toBe('en-US')
    expect(resolveDisplayLocale('auto', undefined)).toBe('en-US')
    expect(resolveDisplayLocale('auto', undefined, 'pt-BR')).toBe('pt-BR')
  })
})

describe('resolveDateOrder', () => {
  it('honours an explicit date format above everything else', () => {
    expect(resolveDateOrder('ymd', 'comma_dot', 'USD')).toBe('ymd')
    expect(resolveDateOrder('dmy', 'comma_dot', 'USD')).toBe('dmy')
    expect(resolveDateOrder('mdy', 'dot_comma', 'BRL')).toBe('mdy')
  })

  it('derives the order from the number format when the date format is auto', () => {
    expect(resolveDateOrder('auto', 'comma_dot', 'BRL')).toBe('mdy')
    expect(resolveDateOrder('auto', 'dot_comma', 'USD')).toBe('dmy')
    expect(resolveDateOrder('auto', 'space_comma', 'USD')).toBe('dmy')
  })

  it('falls back to the currency when both settings are auto', () => {
    expect(resolveDateOrder('auto', 'auto', 'USD')).toBe('mdy')
    expect(resolveDateOrder('auto', 'auto', 'BRL')).toBe('dmy')
    expect(resolveDateOrder('auto', 'auto', 'EUR')).toBe('dmy')
  })

  it('defaults to day-first when nothing is configured', () => {
    // Day-first covers the majority of the supported currency set, so it is
    // the safer default for a user who never opened the settings screen.
    expect(resolveDateOrder(undefined, undefined, undefined)).toBe('dmy')
  })
})

describe('resolveDateLocale', () => {
  it('picks the English regional variant that matches the order', () => {
    expect(resolveDateLocale('mdy', 'auto', 'USD', 'en')).toBe('en-US')
    expect(resolveDateLocale('dmy', 'auto', 'USD', 'en')).toBe('en-GB')
    expect(resolveDateLocale('ymd', 'auto', 'USD', 'en')).toBe('en-CA')
  })

  it('keeps a day-first language as itself, so month names stay translated', () => {
    // The whole point: a pt-BR user on the default order must not get
    // English month names.
    expect(resolveDateLocale('auto', 'auto', 'BRL', 'pt-BR')).toBe('pt-BR')
    expect(resolveDateLocale('dmy', 'auto', 'EUR', 'es')).toBe('es')
  })

  it('borrows an English proxy when a language is paired with a foreign order', () => {
    expect(resolveDateLocale('mdy', 'auto', 'BRL', 'pt-BR')).toBe('en-US')
    expect(resolveDateLocale('ymd', 'auto', 'BRL', 'pt-BR')).toBe('en-CA')
  })

  it('treats any en- variant as English', () => {
    expect(resolveDateLocale('dmy', 'auto', 'USD', 'en-GB')).toBe('en-GB')
  })
})

describe('formatCurrency', () => {
  it('renders an em dash for a missing value rather than "0"', () => {
    // A null balance means "unknown", and showing 0 would be a lie.
    expect(formatCurrency(null)).toBe('—')
    expect(formatCurrency(undefined)).toBe('—')
  })

  it('formats with the separators of the given locale', () => {
    expect(normalize(formatCurrency(1234.5, 'USD', 'en-US'))).toBe('$1,234.50')
    expect(normalize(formatCurrency(1234.5, 'BRL', 'pt-BR'))).toBe('R$ 1.234,50')
    expect(normalize(formatCurrency(1234.5, 'EUR', 'de-DE'))).toBe('1.234,50 €')
  })

  it('keeps each currency at its own precision', () => {
    // Pinning two decimals would render yen as "￥1,000.00".
    expect(normalize(formatCurrency(1000, 'JPY', 'ja-JP'))).toBe('￥1,000')
    expect(normalize(formatCurrency(1000, 'CLP', 'es-CL'))).toBe('$1.000')
  })

  it('renders negatives with a sign', () => {
    expect(normalize(formatCurrency(-42, 'USD', 'en-US'))).toBe('-$42.00')
  })

  it('never renders a negative zero', () => {
    // -0 reaches this from a summed balance that cancels out. "-R$ 0,00" on a
    // dashboard reads as a bug to the user.
    expect(normalize(formatCurrency(-0, 'BRL', 'pt-BR'))).toBe('R$ 0,00')
    expect(normalize(formatCurrency(-0, 'USD', 'en-US'))).toBe('$0.00')
  })

  it('swallows sub-cent residue left by FX conversion instead of signing it', () => {
    expect(normalize(formatCurrency(-0.001, 'USD', 'en-US'))).toBe('$0.00')
    expect(normalize(formatCurrency(-0.004, 'USD', 'en-US'))).toBe('$0.00')
  })

  it('still signs a value that rounds to a real cent', () => {
    expect(normalize(formatCurrency(-0.006, 'USD', 'en-US'))).toBe('-$0.01')
  })

  it('applies the zero rule at the currency precision, not always two decimals', () => {
    // JPY has no minor unit, so anything under half a yen is zero here.
    expect(normalize(formatCurrency(-0.4, 'JPY', 'ja-JP'))).toBe('￥0')
  })

  it('falls back to USD in en-US when the locale is malformed', () => {
    // i18next reads ?lng= from the querystring, so a hand-typed URL can put
    // junk in here. Throwing would blank the whole screen.
    expect(normalize(formatCurrency(10, 'USD', 'not a locale'))).toBe('$10.00')
  })

  it('falls back when the currency code is malformed', () => {
    expect(normalize(formatCurrency(10, 'not-a-currency', 'en-US'))).toBe('$10.00')
  })

  it('treats an empty currency as USD', () => {
    expect(normalize(formatCurrency(10, '', 'en-US'))).toBe('$10.00')
  })

  it('defaults to USD in en-US with no options', () => {
    expect(normalize(formatCurrency(5))).toBe('$5.00')
  })

  it('formats zero without a sign', () => {
    expect(normalize(formatCurrency(0, 'USD', 'en-US'))).toBe('$0.00')
  })
})
