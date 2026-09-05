import { describe, it, expect } from 'vitest'
import { taxIdCountry, taxIdFlag } from './tax-id-country'

// The shape the server sends in /api/fiscal/tax-id-kinds, trimmed to what
// these cases need.
const JURISDICTIONS = [
  { code: 'BR', kinds: ['cnpj', 'cpf', 'ie', 'im'] },
  { code: 'CL', kinds: ['cl_rut'] },
  { code: 'UY', kinds: ['uy_rut'] },
  { code: 'DE', kinds: ['vat', 'steuernummer'] },
  { code: 'NL', kinds: ['vat'] },
  { code: 'GR', kinds: ['vat'] },
  { code: 'GB', kinds: ['vat'] },
  { code: 'IN', kinds: ['gstin', 'pan'] },
]

describe('taxIdCountry', () => {
  it('places a document that only one country asks for', () => {
    expect(taxIdCountry('cnpj', '11222333000181', JURISDICTIONS)).toBe('BR')
    expect(taxIdCountry('steuernummer', '1234567890', JURISDICTIONS)).toBe('DE')
    expect(taxIdCountry('gstin', '27AAACR5055K1Z2', JURISDICTIONS)).toBe('IN')
  })

  it('tells the two RUTs apart, which share a name but not a country', () => {
    expect(taxIdCountry('cl_rut', '609100001', JURISDICTIONS)).toBe('CL')
    expect(taxIdCountry('uy_rut', '211003420011', JURISDICTIONS)).toBe('UY')
  })

  it('reads a VAT id’s country out of the number itself', () => {
    // The case that motivated this: one kind, nineteen countries, so the
    // label cannot say whose it is but the value can.
    expect(taxIdCountry('vat', 'NL802225395B01', JURISDICTIONS)).toBe('NL')
    expect(taxIdCountry('vat', 'DE123456789', JURISDICTIONS)).toBe('DE')
    expect(taxIdCountry('vat', 'IT12345678901', JURISDICTIONS)).toBe('IT')
  })

  it('knows the VAT prefixes that are not the country code', () => {
    // Greece bills under EL, and a naive two-letter read would emit a flag
    // for a country that does not exist.
    expect(taxIdCountry('vat', 'EL123456789', JURISDICTIONS)).toBe('GR')
    // Northern Ireland kept an EU-facing prefix after Brexit.
    expect(taxIdCountry('vat', 'XI123456789', JURISDICTIONS)).toBe('GB')
  })

  it('says nothing rather than guessing on a half-typed VAT id', () => {
    for (const value of ['', 'D', '12345', '1234567890']) {
      expect(taxIdCountry('vat', value, JURISDICTIONS)).toBeNull()
    }
  })

  it('refuses a prefix that is not a real region', () => {
    expect(taxIdCountry('vat', 'ZZ123456789', JURISDICTIONS)).toBeNull()
    expect(taxIdCountry('vat', 'QQ999', JURISDICTIONS)).toBeNull()
  })

  it('never places an `other` document', () => {
    // `other` means the product does not know what this is. Reading a country
    // off the first two characters of an unknown string would look confident
    // and be wrong.
    expect(taxIdCountry('other', 'BP/1234/2024', JURISDICTIONS)).toBeNull()
    expect(taxIdCountry('other', 'US-12345', JURISDICTIONS)).toBeNull()
  })

  it('says nothing for a kind no pack lists', () => {
    expect(taxIdCountry('siret', '12345678901234', JURISDICTIONS)).toBeNull()
    expect(taxIdCountry('', '', JURISDICTIONS)).toBeNull()
  })
})

describe('taxIdFlag', () => {
  it('renders the flag for a placed document', () => {
    expect(taxIdFlag('cnpj', '11222333000181', JURISDICTIONS)).toBe('🇧🇷')
    expect(taxIdFlag('vat', 'NL802225395B01', JURISDICTIONS)).toBe('🇳🇱')
    expect(taxIdFlag('vat', 'EL123456789', JURISDICTIONS)).toBe('🇬🇷')
  })

  it('renders nothing rather than a placeholder when the country is unknown', () => {
    expect(taxIdFlag('vat', '', JURISDICTIONS)).toBe('')
    expect(taxIdFlag('other', 'anything', JURISDICTIONS)).toBe('')
  })
})
