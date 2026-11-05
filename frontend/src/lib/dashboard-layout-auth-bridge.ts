import type { QueryClient } from '@tanstack/react-query'

export type DashboardOwnerToken = symbol
export type DashboardAuthSnapshot = Readonly<{ generation: number; revision: number }>
export type RetirementReason = 'authentication' | 'displaced' | 'identity' | 'teardown'
export type DashboardRegistration = Readonly<{ isCurrent(): boolean; release(): void }>

type Retirement = (reason: RetirementReason) => void
interface Entry {
  owner: DashboardOwnerToken
  generation: number
  retire: Retirement
  retired: boolean
  leases: number
  ticket: symbol | null
}
interface Metadata {
  snapshot: DashboardAuthSnapshot
  entry: Entry | null
  depth: number
  scheduled: boolean
  listeners: Set<{ listener: () => void }>
}

const ZERO: DashboardAuthSnapshot = Object.freeze({ generation: 0, revision: 0 })
const clients = new WeakMap<QueryClient, Metadata>()
const provenance = new WeakMap<DashboardRegistration, { metadata: Metadata; entry: Entry }>()

function validateClient(client: QueryClient): void {
  if (typeof client !== 'object' || client === null) {
    throw new Error('Invalid dashboard authentication client')
  }
}

function metadataFor(client: QueryClient): Metadata {
  let metadata = clients.get(client)
  if (!metadata) {
    metadata = { snapshot: ZERO, entry: null, depth: 0, scheduled: false, listeners: new Set() }
    clients.set(client, metadata)
  }
  return metadata
}

export function getDashboardAuthSnapshot(client: QueryClient): DashboardAuthSnapshot {
  return clients.get(client)?.snapshot ?? ZERO
}

function commit(metadata: Metadata, generation = metadata.snapshot.generation): void {
  metadata.snapshot = Object.freeze({ generation, revision: metadata.snapshot.revision + 1 })
  if (metadata.scheduled) return
  metadata.scheduled = true
  queueMicrotask(() => {
    metadata.scheduled = false
    for (const subscription of [...metadata.listeners]) {
      if (!metadata.listeners.has(subscription)) continue
      try {
        subscription.listener()
      } catch {
        // Only synchronous observer failures are isolated.
      }
    }
  })
}

export function subscribeDashboardAuth(client: QueryClient, listener: () => void): () => void {
  validateClient(client)
  if (typeof listener !== 'function') throw new Error('Invalid dashboard authentication listener')
  const metadata = metadataFor(client)
  const subscription = { listener }
  metadata.listeners.add(subscription)
  return () => { metadata.listeners.delete(subscription) }
}

function detach(metadata: Metadata, entry: Entry, reason: RetirementReason): void {
  if (metadata.entry !== entry || entry.retired) return
  metadata.entry = null
  entry.retired = true
  entry.ticket = null
  commit(metadata)
  try {
    entry.retire(reason)
  } catch {
    // The entry is already detached; synchronous failure cannot restore it.
  }
}

export function revokeDashboardAuthentication(client: QueryClient): void {
  validateClient(client)
  const metadata = metadataFor(client)
  metadata.depth += 1
  try {
    commit(metadata, metadata.snapshot.generation + 1)
    if (metadata.entry) detach(metadata, metadata.entry, 'authentication')
  } finally {
    metadata.depth -= 1
  }
}

function acquire(metadata: Metadata, entry: Entry): DashboardRegistration {
  entry.ticket = null
  entry.leases += 1
  let released = false
  const registration: DashboardRegistration = Object.freeze({
    // Entry capability survives release until the actual guarded detach.
    isCurrent: () => metadata.entry === entry && !entry.retired &&
      metadata.snapshot.generation === entry.generation,
    release: () => {
      if (released) return
      released = true
      entry.leases -= 1
      if (entry.leases !== 0 || metadata.entry !== entry || entry.retired) return
      const ticket = Symbol('dashboard teardown')
      entry.ticket = ticket
      queueMicrotask(() => {
        if (metadata.entry !== entry || entry.retired || entry.leases !== 0 ||
            entry.ticket !== ticket || metadata.snapshot.generation !== entry.generation) return
        metadata.depth += 1
        try {
          detach(metadata, entry, 'teardown')
        } finally {
          metadata.depth -= 1
        }
      })
    },
  })
  provenance.set(registration, { metadata, entry })
  return registration
}

export function registerDashboardOwner(
  client: QueryClient,
  generation: number,
  owner: DashboardOwnerToken,
  retire: Retirement,
  replacing?: DashboardRegistration,
): DashboardRegistration | null {
  validateClient(client)
  if (!Number.isSafeInteger(generation) || generation < 0 || typeof owner !== 'symbol' ||
      typeof retire !== 'function' ||
      (replacing !== undefined && (typeof replacing !== 'object' || replacing === null))) {
    throw new Error('Invalid dashboard owner registration')
  }
  const existing = clients.get(client)
  if (existing?.entry?.owner === owner && existing.entry.generation === generation &&
      existing.entry.retire !== retire) {
    throw new Error('Dashboard owner retirement callback must remain stable')
  }
  if (generation !== (existing?.snapshot.generation ?? 0) || (existing?.depth ?? 0) !== 0) return null
  if (replacing !== undefined) {
    const issued = provenance.get(replacing)
    if (!issued || issued.metadata !== existing || issued.entry !== existing?.entry ||
        issued.entry.retired || issued.entry.generation !== generation) return null
  }
  const metadata = existing ?? metadataFor(client)
  const current = metadata.entry
  if (current?.owner === owner) return acquire(metadata, current)
  metadata.depth += 1
  try {
    if (current) detach(metadata, current, replacing === undefined ? 'displaced' : 'identity')
    // Retirement may synchronously pulse authentication, including nested pulses.
    if (metadata.snapshot.generation !== generation) return null
    const entry: Entry = { owner, generation, retire, retired: false, leases: 0, ticket: null }
    metadata.entry = entry
    commit(metadata)
    return acquire(metadata, entry)
  } finally {
    metadata.depth -= 1
  }
}
