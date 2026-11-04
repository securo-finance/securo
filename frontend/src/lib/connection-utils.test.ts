import { describe, expect, it } from 'vitest'
import { getConnectionName } from './connection-utils'

const t = (key: string, opts?: Record<string, unknown>) =>
  `${key}:${opts?.provider}:${opts?.count}`

describe('getConnectionName', () => {
  it('a user rename always wins', () => {
    expect(
      getConnectionName(
        {
          provider: 'simplefin',
          institution_name: 'First Bank',
          display_name: 'My Banks',
          institutions: [{ name: 'First Bank' }, { name: 'Second Brokerage' }],
        },
        t,
      ),
    ).toBe('My Banks')
  })

  it('labels a multi-institution link by its provider, not one bank', () => {
    expect(
      getConnectionName(
        {
          provider: 'simplefin',
          institution_name: 'First Bank',
          institutions: [{ name: 'First Bank' }, { name: 'Second Brokerage' }],
        },
        t,
      ),
    ).toBe('accounts.multiInstitutionLink:SimpleFIN:2')
  })

  it('falls back to the raw provider key when unmapped', () => {
    expect(
      getConnectionName(
        {
          provider: 'newprovider',
          institution_name: 'X',
          institutions: [{ name: 'A' }, { name: 'B' }],
        },
        t,
      ),
    ).toBe('accounts.multiInstitutionLink:newprovider:2')
  })

  it('a single-institution link shows that institution', () => {
    expect(
      getConnectionName(
        { provider: 'simplefin', institution_name: 'Old Label', institutions: [{ name: 'First Bank' }] },
        t,
      ),
    ).toBe('First Bank')
  })

  it('no institutions falls back to the connection label (Pluggy/Enable)', () => {
    expect(
      getConnectionName({ provider: 'pluggy', institution_name: 'Nubank', institutions: [] }, t),
    ).toBe('Nubank')
    expect(getConnectionName({ institution_name: 'Nubank' }, t)).toBe('Nubank')
  })
})
