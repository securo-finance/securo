export type ParsedSemver = {
  major: number
  minor: number
  patch: number
  prerelease: string[]
}

const SEMVER_RE = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/

export function parseSemver(value: string | null | undefined): ParsedSemver | null {
  if (!value) return null
  const match = value.trim().match(SEMVER_RE)
  if (!match) return null
  return {
    major: Number.parseInt(match[1], 10),
    minor: Number.parseInt(match[2], 10),
    patch: Number.parseInt(match[3], 10),
    prerelease: match[4]?.split('.').filter(Boolean) ?? [],
  }
}

function comparePrereleaseIds(left: string, right: string): number {
  const leftNum = /^\d+$/.test(left)
  const rightNum = /^\d+$/.test(right)
  if (leftNum && rightNum) {
    return Number.parseInt(left, 10) - Number.parseInt(right, 10)
  }
  if (leftNum) return -1
  if (rightNum) return 1
  return left.localeCompare(right)
}

export function compareSemver(a: ParsedSemver, b: ParsedSemver): number {
  if (a.major !== b.major) return a.major - b.major
  if (a.minor !== b.minor) return a.minor - b.minor
  if (a.patch !== b.patch) return a.patch - b.patch

  const aStable = a.prerelease.length === 0
  const bStable = b.prerelease.length === 0
  if (aStable && bStable) return 0
  if (aStable) return 1
  if (bStable) return -1

  const max = Math.max(a.prerelease.length, b.prerelease.length)
  for (let i = 0; i < max; i += 1) {
    const ai = a.prerelease[i]
    const bi = b.prerelease[i]
    if (ai === undefined) return -1
    if (bi === undefined) return 1
    const diff = comparePrereleaseIds(ai, bi)
    if (diff !== 0) return diff
  }
  return 0
}

export function isUpdateAvailable(current: string | null | undefined, latest: string | null | undefined): boolean {
  const c = parseSemver(current)
  const l = parseSemver(latest)
  if (!c || !l) return false
  return compareSemver(l, c) > 0
}
