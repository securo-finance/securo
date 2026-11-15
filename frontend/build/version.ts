import { execSync, execFileSync } from 'node:child_process'
const officialReleaseRepo = 'https://github.com/securo-finance/securo.git'
const officialLatestReleaseApi = 'https://api.github.com/repos/securo-finance/securo/releases/latest'
const semverPattern = /^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/

type ParsedSemverTag = {
  major: number
  minor: number
  patch: number
  prerelease: string[]
}

function runLocalGit(projectRoot: string, command: string) {
  const args = ['-c', `safe.directory=${projectRoot}`, ...command.split(' ')]
  return execFileSync('git', args, {
    cwd: projectRoot,
    stdio: ['ignore', 'pipe', 'ignore'],
  })
    .toString()
    .trim()
}

function normalizeVersionLabel(value?: string | null) {
  const trimmed = value?.trim()
  if (!trimmed) return null

  if (semverPattern.test(trimmed)) {
    return trimmed.startsWith('v') ? trimmed : `v${trimmed}`
  }

  return trimmed
}

function parseSemverTag(value: string): ParsedSemverTag | null {
  const match = value.match(/^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/)
  if (!match) return null

  return {
    major: Number.parseInt(match[1], 10),
    minor: Number.parseInt(match[2], 10),
    patch: Number.parseInt(match[3], 10),
    prerelease: match[4]?.split('.').filter(Boolean) ?? [],
  }
}

function comparePrereleaseIdentifiers(left: string, right: string) {
  const leftIsNumber = /^\d+$/.test(left)
  const rightIsNumber = /^\d+$/.test(right)

  if (leftIsNumber && rightIsNumber) {
    return Number.parseInt(left, 10) - Number.parseInt(right, 10)
  }

  if (leftIsNumber) return -1
  if (rightIsNumber) return 1

  return left.localeCompare(right)
}

function compareSemverTags(left: ParsedSemverTag, right: ParsedSemverTag) {
  if (left.major !== right.major) return left.major - right.major
  if (left.minor !== right.minor) return left.minor - right.minor
  if (left.patch !== right.patch) return left.patch - right.patch

  const leftStable = left.prerelease.length === 0
  const rightStable = right.prerelease.length === 0
  if (leftStable && rightStable) return 0
  if (leftStable) return 1
  if (rightStable) return -1

  const maxLength = Math.max(left.prerelease.length, right.prerelease.length)
  for (let index = 0; index < maxLength; index += 1) {
    const leftId = left.prerelease[index]
    const rightId = right.prerelease[index]

    if (leftId === undefined) return -1
    if (rightId === undefined) return 1

    const difference = comparePrereleaseIdentifiers(leftId, rightId)
    if (difference !== 0) return difference
  }

  return 0
}

function getCurrentGitTag(projectRoot: string) {
  try {
    const tag = runLocalGit(projectRoot, 'describe --tags --exact-match')

    if (!parseSemverTag(tag)) return null
    return normalizeVersionLabel(tag)
  } catch {
    return null
  }
}

function getHeadShortSha(projectRoot: string) {
  try {
    const shortSha = runLocalGit(projectRoot, 'rev-parse --short HEAD')

    if (!shortSha) return null

    return shortSha
  } catch {
    return null
  }
}

function getOfficialMainSha(projectRoot: string) {
  try {
    const output = execSync(`git ls-remote ${officialReleaseRepo} refs/heads/main`, {
      cwd: projectRoot,
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 3000,
    })
      .toString()
      .trim()

    const [sha] = output.split(/\s+/)
    return sha || null
  } catch {
    return null
  }
}

function getHeadSha(projectRoot: string) {
  try {
    const sha = runLocalGit(projectRoot, 'rev-parse HEAD')

    return sha || null
  } catch {
    return null
  }
}

function getLatestOfficialReleaseTagFromGit(projectRoot: string) {
  try {
    const output = execSync(`git ls-remote --refs --tags ${officialReleaseRepo} 'v*'`, {
      cwd: projectRoot,
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 3000,
    }).toString()

    const semverTags = output
      .split('\n')
      .map((line) => line.split('refs/tags/')[1]?.trim())
      .filter((tag): tag is string => Boolean(tag))
      .map((tag) => {
        const normalizedTag = normalizeVersionLabel(tag)
        const parsedTag = normalizedTag ? parseSemverTag(normalizedTag) : null

        if (!normalizedTag || !parsedTag) return null

        return {
          normalizedTag,
          parsedTag,
        }
      })
      .filter((entry): entry is { normalizedTag: string; parsedTag: ParsedSemverTag } => Boolean(entry))

    if (semverTags.length === 0) return null

    const stableTags = semverTags.filter((entry) => entry.parsedTag.prerelease.length === 0)
    const candidateTags = stableTags.length > 0 ? stableTags : semverTags
    candidateTags.sort((left, right) => compareSemverTags(left.parsedTag, right.parsedTag))

    return candidateTags.at(-1)?.normalizedTag ?? null
  } catch {
    return null
  }
}

async function getLatestOfficialReleaseTagFromApi() {
  try {
    const response = await fetch(officialLatestReleaseApi, {
      headers: {
        Accept: 'application/vnd.github+json',
        'User-Agent': 'securo-frontend-build',
      },
      signal: AbortSignal.timeout(3000),
    })

    if (!response.ok) return null

    const payload = (await response.json()) as { tag_name?: string }
    return normalizeVersionLabel(payload.tag_name)
  } catch {
    return null
  }
}

async function getLatestOfficialReleaseTag(projectRoot: string) {
  return getLatestOfficialReleaseTagFromGit(projectRoot) ?? (await getLatestOfficialReleaseTagFromApi())
}

export async function resolveAppVersion(projectRoot: string, explicitVersion?: string) {
  const resolvedExplicitVersion = normalizeVersionLabel(explicitVersion)
  if (resolvedExplicitVersion) return resolvedExplicitVersion

  const currentGitTag = getCurrentGitTag(projectRoot)
  if (currentGitTag) return currentGitTag

  const latestOfficialReleaseTag = await getLatestOfficialReleaseTag(projectRoot)
  const headSha = getHeadSha(projectRoot)
  const officialMainSha = getOfficialMainSha(projectRoot)
  const headShortSha = getHeadShortSha(projectRoot)

  if (latestOfficialReleaseTag) {
    if (headSha && officialMainSha && headSha === officialMainSha) {
      return latestOfficialReleaseTag
    }

    if (headShortSha) {
      return `${latestOfficialReleaseTag}+${headShortSha}`
    }

    return latestOfficialReleaseTag
  }

  if (headShortSha) return headShortSha

  return 'dev'
}
