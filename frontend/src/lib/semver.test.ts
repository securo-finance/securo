import { describe, expect, it } from 'vitest'

import { compareSemver, isUpdateAvailable, parseSemver } from '@/lib/semver'

describe('parseSemver', () => {
  it('parses a plain version', () => {
    expect(parseSemver('1.2.3')).toEqual({
      major: 1,
      minor: 2,
      patch: 3,
      prerelease: [],
    })
  })

  it('accepts the v prefix that GitHub tags carry', () => {
    expect(parseSemver('v0.14.5')).toMatchObject({
      major: 0,
      minor: 14,
      patch: 5,
    })
  })

  it('trims surrounding whitespace', () => {
    expect(parseSemver('  v1.0.0 ')).toMatchObject({ major: 1 })
  })

  it('splits the prerelease into identifiers', () => {
    expect(parseSemver('1.0.0-rc.2')?.prerelease).toEqual(['rc', '2'])
  })

  it('ignores build metadata', () => {
    expect(parseSemver('1.0.0+build.7')).toMatchObject({
      major: 1,
      prerelease: [],
    })
  })

  it('returns null for anything that is not a version', () => {
    expect(parseSemver('')).toBeNull()
    expect(parseSemver(null)).toBeNull()
    expect(parseSemver(undefined)).toBeNull()
    expect(parseSemver('latest')).toBeNull()
    expect(parseSemver('1.2')).toBeNull()
    expect(parseSemver('1.2.3.4')).toBeNull()
  })
})

describe('compareSemver', () => {
  const parse = (v: string) => parseSemver(v)!

  it('orders by major, then minor, then patch', () => {
    expect(compareSemver(parse('2.0.0'), parse('1.9.9'))).toBeGreaterThan(0)
    expect(compareSemver(parse('1.3.0'), parse('1.2.9'))).toBeGreaterThan(0)
    expect(compareSemver(parse('1.2.4'), parse('1.2.3'))).toBeGreaterThan(0)
  })

  it('treats equal versions as equal', () => {
    expect(compareSemver(parse('1.2.3'), parse('1.2.3'))).toBe(0)
  })

  it('ranks a stable release above its own prerelease', () => {
    expect(compareSemver(parse('1.0.0'), parse('1.0.0-rc.1'))).toBeGreaterThan(0)
    expect(compareSemver(parse('1.0.0-rc.1'), parse('1.0.0'))).toBeLessThan(0)
  })

  it('compares numeric prerelease identifiers numerically', () => {
    // String comparison would put rc.10 below rc.9.
    expect(compareSemver(parse('1.0.0-rc.10'), parse('1.0.0-rc.9'))).toBeGreaterThan(0)
  })

  it('ranks a numeric identifier below an alphanumeric one', () => {
    expect(compareSemver(parse('1.0.0-1'), parse('1.0.0-alpha'))).toBeLessThan(0)
  })

  it('ranks a longer prerelease above its prefix', () => {
    expect(compareSemver(parse('1.0.0-rc.1'), parse('1.0.0-rc'))).toBeGreaterThan(0)
  })
})

describe('isUpdateAvailable', () => {
  it('is true only when the latest release is genuinely newer', () => {
    expect(isUpdateAvailable('0.14.5', '0.14.6')).toBe(true)
    expect(isUpdateAvailable('0.14.5', 'v0.15.0')).toBe(true)
  })

  it('is false when already current or ahead', () => {
    expect(isUpdateAvailable('0.14.5', '0.14.5')).toBe(false)
    expect(isUpdateAvailable('0.15.0', '0.14.5')).toBe(false)
  })

  it('does not offer a prerelease as an update to a stable install', () => {
    expect(isUpdateAvailable('1.0.0', '1.0.0-rc.1')).toBe(false)
  })

  it('offers the stable release to someone on its prerelease', () => {
    expect(isUpdateAvailable('1.0.0-rc.1', '1.0.0')).toBe(true)
  })

  it('stays quiet when either version is unreadable', () => {
    // A dev build reports something that is not a version. Nagging the user
    // with a bogus update banner is worse than saying nothing.
    expect(isUpdateAvailable('dev', '1.0.0')).toBe(false)
    expect(isUpdateAvailable('1.0.0', 'nightly')).toBe(false)
    expect(isUpdateAvailable(null, '1.0.0')).toBe(false)
    expect(isUpdateAvailable('1.0.0', undefined)).toBe(false)
  })
})
