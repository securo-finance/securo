import type { ResourceLanguage } from 'i18next'

import forkEn from '@/locales/fork/en.json'
import forkPtBR from '@/locales/fork/pt-BR.json'

// This fork's strings live in locales/fork/ so the upstream locale files stay
// byte-identical to upstream's and merge without conflicts. A language with
// no fork file falls back to English through i18next's fallbackLng.
const bundled: Record<string, ResourceLanguage> = { en: forkEn, 'pt-BR': forkPtBR }

const loaders = import.meta.glob<{ default: ResourceLanguage }>([
  '../locales/fork/*.json',
  '!../locales/fork/en.json',
  '!../locales/fork/pt-BR.json',
])

type Tree = { [key: string]: unknown }

function isTree(value: unknown): value is Tree {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function merge(base: Tree, fork: Tree): Tree {
  const merged: Tree = { ...base }
  for (const [key, value] of Object.entries(fork)) {
    const current = merged[key]
    merged[key] = isTree(current) && isTree(value) ? merge(current, value) : value
  }
  return merged
}

export function withBundledForkStrings(language: 'en' | 'pt-BR', base: ResourceLanguage): ResourceLanguage {
  return merge(base, bundled[language]) as ResourceLanguage
}

export async function withForkStrings(language: string, base: ResourceLanguage): Promise<ResourceLanguage> {
  const load = loaders[`../locales/fork/${language}.json`]
  return load ? (merge(base, (await load()).default) as ResourceLanguage) : base
}
